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
		doc = frappe.get_doc("Claims To Docx", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}
		if not doc.claims:
			return {"success": False, "error": "Claims 不能为空，请先填写后再运行任务"}
		if doc.is_done:
			return {"success": False, "error": "任务已完成，不可重复运行"}
		if doc.is_running:
			return {"success": False, "error": "任务正在运行中，请等待完成"}
		doc.is_running = 1
		doc.save()
		frappe.db.commit()
		enqueue(
			"patent_hub.api.run_claims_to_docx._job",
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
		doc = frappe.get_doc("Claims To Docx", docname)
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
		app_name = api_endpoint.claims_to_docx.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"请求 URL：{url}")
		# 编码 markdown
		markdown_text = doc.claims or ""
		base64file = base64.b64encode(markdown_text.encode("utf-8")).decode("utf-8")
		# 标题
		patent_title = doc.patent_title
		_title = re.sub(r"[^\w\u4e00-\u9fa5\-]", "", patent_title)  # 去除标点，保留连字符、中文、字母、数字
		# 拼接 tmp_folder
		server_work_dir = api_endpoint.get_password("server_work_dir")
		tmp_folder = os.path.join(server_work_dir, _title, "wf-catd")
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
		frappe.publish_realtime("claims_to_docx_done", {"docname": doc.name}, user=user)
	except Exception as e:
		logger.error(f"任务 {docname} 执行失败: {e!s}")
		logger.error(frappe.get_traceback())
		try:
			# 更新文档状态为失败
			doc = frappe.get_doc("Claims To Docx", docname)
			# error_msg
			error_msg = f"失败: {e!s}"
			short_error_msg = textwrap.shorten(error_msg, width=135, placeholder="...")
			doc.set("generated_files", [{"s3_url": short_error_msg}])
			# 重置运行状态
			doc.is_done = 0
			doc.is_running = 0
			doc.save()
			frappe.db.commit()
			frappe.publish_realtime("claims_to_docx_failed", {"error": str(e), "docname": docname}, user=user)
		except Exception as save_error:
			logger.error(f"保存失败状态时出错: {save_error!s}")


@frappe.whitelist()
def get_file_content(docname: str, file_type: str):
	"""
	获取指定类型文件的内容
	Args:
		docname: Claims To Docx 文档名
		file_type: 文件类型 (markdown, markdown_before_tex, docx)
	Returns:
		文件内容
	"""
	try:
		doc = frappe.get_doc("Claims To Docx", docname)
		# 找到对应的文件
		target_file = None
		for file in doc.generated_files:
			if not file.s3_url:
				continue
			if file_type == "markdown":
				if file.s3_url.endswith("c2d/input_text.txt"):
					target_file = file
					break
			elif file_type == "markdown_before_tex":
				if file.s3_url.endswith("c-tex/input_text.txt"):
					target_file = file
					break
			elif file_type == "docx":
				if file.s3_url.endswith(".docx") and "c2d/" in file.s3_url:
					filename = file.s3_url.split("/").pop()
					excluded_files = ["abstract.docx", "claims.docx", "description.docx", "figures.docx"]
					if filename not in excluded_files:
						target_file = file
						break
		if not target_file:
			return {"success": False, "error": f"未找到 {file_type} 文件"}
		if not target_file.signed_url:
			return {"success": False, "error": "文件链接未生成，请先刷新链接"}
		# 检查链接是否过期
		if target_file.signed_url_generated_at:
			if now_datetime() >= add_to_date(target_file.signed_url_generated_at, hours=1):
				return {"success": False, "error": "文件链接已过期，请先刷新链接"}

		async def fetch_content():
			async with httpx.AsyncClient(
				timeout=30,
				follow_redirects=True,
				verify=False,  # 如果有SSL证书问题可以设置为False
			) as client:
				response = await client.get(target_file.signed_url)
				response.raise_for_status()
				# 根据文件类型处理内容
				if file_type == "docx":
					return response.content  # 返回二进制内容
				else:
					return response.text  # 返回文本内容

		content = asyncio.run(fetch_content())
		return {"success": True, "content": content}
	except httpx.HTTPError as e:
		logger.error(f"HTTP请求失败: {e}")
		return {"success": False, "error": f"HTTP请求失败: {e!s}"}
	except asyncio.TimeoutError:
		logger.error("请求超时")
		return {"success": False, "error": "请求超时，请稍后重试"}
	except Exception as e:
		logger.error(f"获取文件内容失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"获取文件内容失败: {e!s}"}
