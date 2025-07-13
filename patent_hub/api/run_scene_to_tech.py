import asyncio
import base64
import json
import logging
import os
import re
import textwrap

import boto3
import frappe
import httpx
from frappe import enqueue
from frappe.utils import add_to_date, now_datetime

logger = frappe.logger("app_patent_hub")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname):
	try:
		logger.info(f"开始处理文档：{docname}")
		doc = frappe.get_doc("Scene To Tech", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}
		if not doc.scene:
			return {"success": False, "error": "Scene 不能为空，请先填写后再运行任务"}
		if doc.is_done:
			return {"success": False, "error": "任务已完成，不可重复运行"}
		if doc.is_running:
			return {"success": False, "error": "任务正在运行中，请等待完成"}
		doc.is_running = 1
		doc.save()
		frappe.db.commit()
		enqueue(
			"patent_hub.api.run_scene_to_tech._job",
			queue="long",
			timeout=TIMEOUT,
			docname=docname,
			user=frappe.session.user,
		)
		return {"success": True, "message": "任务已成功提交"}
	except Exception as e:
		logger.error(f"启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e}"}


def _job(docname, user=None):
	logger.info(f"进入 job: {docname}")
	try:
		doc = frappe.get_doc("Scene To Tech", docname)
		if not doc:
			frappe.throw(f"文档 {docname} 不存在")
		# 确保任务开始时设置正确的状态
		doc.is_running = 1
		doc.is_done = 0
		doc.save()
		frappe.db.commit()
		# 请求 URL
		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")
		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.scene_to_tech.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"请求 URL：{url}")
		# 编码 markdown
		markdown_text = doc.scene or ""
		base64file = base64.b64encode(markdown_text.encode("utf-8")).decode("utf-8")
		# 标题
		patent_title = doc.patent_title
		_title = re.sub(r"[^\w\u4e00-\u9fa5\-]", "", patent_title)  # 去除标点，保留连字符、中文、字母、数字
		# 拼接 tmp_folder
		server_work_dir = api_endpoint.get_password("server_work_dir")
		tmp_folder = os.path.join(server_work_dir, _title, "s2t")
		# payload
		payload = {
			"input": {"base64file": base64file, "patent_title": patent_title, "tmp_folder": tmp_folder}
		}

		async def call_chain():
			async with httpx.AsyncClient(timeout=TIMEOUT) as client:
				return await client.post(url, json=payload)

		res = asyncio.run(call_chain())
		res.raise_for_status()
		res_json = res.json()
		# output
		output = json.loads(res_json["output"])
		logger.info(f"解析后的 JSON: {output}")
		doc.time_s = output.get("TIME(s)", 0.0)
		doc.cost = output.get("cost", 0)
		# s3_urls
		s3_urls = output.get("generated_files", [])
		logger.info(f"S3 URL：{s3_urls}")
		doc.set("generated_files", [{"s3_url": u} for u in s3_urls])
		doc.is_done = 1
		doc.is_running = 0
		doc.save()
		frappe.db.commit()
		frappe.publish_realtime("scene_to_tech_done", {"docname": doc.name}, user=user)
	except Exception as e:
		logger.error(f"任务 {docname} 执行失败: {e!s}")
		logger.error(frappe.get_traceback())
		try:
			# 更新文档状态为失败
			doc = frappe.get_doc("Scene To Tech", docname)
			# error_msg
			error_msg = f"失败: {e!s}"
			short_error_msg = textwrap.shorten(error_msg, width=135, placeholder="...")
			doc.set("generated_files", [{"s3_url": short_error_msg}])
			# 重置运行状态
			doc.is_done = 0
			doc.is_running = 0
			doc.save()
			frappe.db.commit()
			frappe.publish_realtime("scene_to_tech_failed", {"error": str(e), "docname": docname}, user=user)
		except Exception as save_error:
			logger.error(f"保存失败状态时出错: {save_error!s}")


def extract_s3_key_from_full_path(s3_full_path: str, bucket_name: str) -> str:
	"""
	Extracsts the S3 object key from a full S3 path given the bucket name.
	Assumes the full path starts with "s3://{bucket_name}/".
	Args:
		s3_full_path: The full S3 path string (e.g., "s3://my-bucket/folder/file.txt").
		bucket_name: The name of the S3 bucket.
	Returns:
		The S3 object key (e.g., "folder/file.txt").
		Returns an empty string if the format doesn't match the expected prefix.
	"""
	expected_prefix = f"s3://{bucket_name}/"
	if s3_full_path.startswith(expected_prefix):
		return s3_full_path[len(expected_prefix) :]
	else:
		# Log a warning if the full path doesn't match the expected format
		logger.warning(
			f"S3 full path '{s3_full_path}' does not start with expected prefix 's3://{bucket_name}/'."
			" Returning empty string as key, which will prevent signed URL generation."
		)
		return ""


@frappe.whitelist()
def generate_signed_urls(docname: str):
	doc = frappe.get_doc("Scene To Tech", docname)
	api_key = frappe.get_single("API KEY")
	if not api_key:
		frappe.throw("未配置 API KEY")
	aws_access_key_id = api_key.get_password("aws_access_key_id")
	aws_secret_access_key = api_key.get_password("aws_secret_access_key")
	aws_region = api_key.aws_region
	s3_bucket_name = api_key.s3_bucket_name
	# Only initialize client if credentials are available
	if not all([aws_access_key_id, aws_secret_access_key, aws_region, s3_bucket_name]):
		frappe.throw("AWS S3 configuration is incomplete. Please check API KEY settings.")
	client = boto3.client(
		"s3",
		aws_access_key_id=aws_access_key_id,
		aws_secret_access_key=aws_secret_access_key,
		region_name=aws_region,
	)
	updated = False
	for file in doc.generated_files:
		if file.signed_url_generated_at:
			if now_datetime() < add_to_date(file.signed_url_generated_at, hours=1):
				continue  # 已生成，未过期，跳过
		if not file.s3_url:
			continue
		# Extract the S3 key from the full path in file.s3_url
		s3_object_key = extract_s3_key_from_full_path(file.s3_url, s3_bucket_name)
		if not s3_object_key:
			_warning = f"S3 URL '{file.s3_url}' 的格式与预期的 's3://bucket_name/key' 不符或无效，跳过签名 URL 的生成。"
			logger.warning(_warning)
			frappe.msgprint(_warning, alert=True)
			continue  # Skip if key extraction fails
		try:
			url = client.generate_presigned_url(
				"get_object",
				Params={"Bucket": s3_bucket_name, "Key": s3_object_key},  # Use the extracted key
				ExpiresIn=3600,
			)
			file.signed_url = url
			file.signed_url_generated_at = now_datetime()
			updated = True
		except Exception as e:
			logger.error(f"Error generating presigned URL for key '{s3_object_key}': {e}")
			# Optionally, you can set an error message in file.signed_url or another field
			file.signed_url = f"Error: {e!s}"
			updated = True  # Mark as updated to save the error status
	if updated:
		doc.save(ignore_permissions=True)
	return {"success": True}
