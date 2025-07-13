import base64
import logging
import os
import re
import tempfile
from datetime import datetime

import boto3
import frappe
from frappe.utils import add_to_date, now_datetime

logger = frappe.logger("app_patent_hub")
logger.setLevel(logging.INFO)


@frappe.whitelist()
def upload_files(docname):
	"""上传 Final Markdown 和 Final Docx 文件到 S3"""
	try:
		logger.info(f"开始上传文件：{docname}")
		doc = frappe.get_doc("Upload Final Docx", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}
		# 检查是否有文件需要上传
		has_markdown = doc.final_markdown
		has_docx = doc.final_docx
		if not has_markdown and not has_docx:
			return {"success": False, "error": "没有文件需要上传"}
		# 获取 AWS 配置
		api_key = frappe.get_single("API KEY")
		if not api_key:
			return {"success": False, "error": "未配置 API KEY"}
		aws_access_key_id = api_key.get_password("aws_access_key_id")
		aws_secret_access_key = api_key.get_password("aws_secret_access_key")
		aws_region = api_key.aws_region
		s3_bucket_name = api_key.s3_bucket_name
		if not all([aws_access_key_id, aws_secret_access_key, aws_region, s3_bucket_name]):
			return {"success": False, "error": "AWS S3 配置不完整"}
		# 初始化 S3 客户端
		s3_client = boto3.client(
			"s3",
			aws_access_key_id=aws_access_key_id,
			aws_secret_access_key=aws_secret_access_key,
			region_name=aws_region,
		)
		# 生成 S3 路径前缀
		patent_title = doc.patent_title or "untitled"
		_title = re.sub(r"[^\w\u4e00-\u9fa5\-]", "", patent_title)  # 去除标点，保留连字符、中文、字母、数字
		timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
		s3_prefix = f"{_title}/upload_final_docx/{timestamp}"
		uploaded_files = []
		# 上传 Final Markdown
		if has_markdown:
			try:
				markdown_file = frappe.get_doc("File", has_markdown)
				file_path = frappe.get_site_path() + markdown_file.file_url
				# 读取文件内容
				with open(file_path, "rb") as f:
					file_content = f.read()
				# 确定文件扩展名
				original_filename = markdown_file.file_name
				if original_filename.lower().endswith(".md"):
					s3_key = f"{s3_prefix}/final_markdown.md"
				else:
					s3_key = f"{s3_prefix}/final_markdown.txt"
				# 上传到 S3
				s3_client.put_object(
					Bucket=s3_bucket_name, Key=s3_key, Body=file_content, ContentType="text/plain"
				)
				s3_url = f"s3://{s3_bucket_name}/{s3_key}"
				uploaded_files.append(s3_url)
				logger.info(f"Final Markdown 上传成功: {s3_url}")
			except Exception as e:
				logger.error(f"上传 Final Markdown 失败: {e}")
				return {"success": False, "error": f"上传 Final Markdown 失败: {e}"}
		# 上传 Final Docx
		if has_docx:
			try:
				docx_file = frappe.get_doc("File", has_docx)
				file_path = frappe.get_site_path() + docx_file.file_url
				# 读取文件内容
				with open(file_path, "rb") as f:
					file_content = f.read()
				s3_key = f"{s3_prefix}/final_docx.docx"
				# 上传到 S3
				s3_client.put_object(
					Bucket=s3_bucket_name,
					Key=s3_key,
					Body=file_content,
					ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
				)
				s3_url = f"s3://{s3_bucket_name}/{s3_key}"
				uploaded_files.append(s3_url)
				logger.info(f"Final Docx 上传成功: {s3_url}")
			except Exception as e:
				logger.error(f"上传 Final Docx 失败: {e}")
				return {"success": False, "error": f"上传 Final Docx 失败: {e}"}
		# 更新文档的 generated_files 表
		if uploaded_files:
			# 清空现有的 generated_files（如果需要保留，可以注释这行）
			doc.set("generated_files", [])
			# 添加新上传的文件
			for s3_url in uploaded_files:
				doc.append("generated_files", {"s3_url": s3_url})
			doc.is_done = 1  # 标记为完成
			doc.save()
			frappe.db.commit()
		return {"success": True, "message": f"成功上传 {len(uploaded_files)} 个文件"}
	except Exception as e:
		logger.error(f"上传文件失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"上传文件失败: {e}"}


def extract_s3_key_from_full_path(s3_full_path: str, bucket_name: str) -> str:
	"""
	从完整的 S3 路径中提取 S3 对象键
	"""
	expected_prefix = f"s3://{bucket_name}/"
	if s3_full_path.startswith(expected_prefix):
		return s3_full_path[len(expected_prefix) :]
	else:
		logger.warning(
			f"S3 full path '{s3_full_path}' does not start with expected prefix 's3://{bucket_name}/'."
			" Returning empty string as key, which will prevent signed URL generation."
		)
		return ""


@frappe.whitelist()
def generate_signed_urls(docname: str):
	"""为上传的文件生成签名URL"""
	try:
		doc = frappe.get_doc("Upload Final Docx", docname)
		# 获取 AWS 配置
		api_key = frappe.get_single("API KEY")
		if not api_key:
			frappe.throw("未配置 API KEY")
		aws_access_key_id = api_key.get_password("aws_access_key_id")
		aws_secret_access_key = api_key.get_password("aws_secret_access_key")
		aws_region = api_key.aws_region
		s3_bucket_name = api_key.s3_bucket_name
		# 检查配置完整性
		if not all([aws_access_key_id, aws_secret_access_key, aws_region, s3_bucket_name]):
			frappe.throw("AWS S3 configuration is incomplete. Please check API KEY settings.")
		# 初始化 S3 客户端
		client = boto3.client(
			"s3",
			aws_access_key_id=aws_access_key_id,
			aws_secret_access_key=aws_secret_access_key,
			region_name=aws_region,
		)
		updated = False
		for file in doc.generated_files:
			# 检查是否需要重新生成签名URL（如果超过1小时或没有生成过）
			if file.signed_url_generated_at:
				if now_datetime() < add_to_date(file.signed_url_generated_at, hours=1):
					continue  # 已生成，未过期，跳过
			if not file.s3_url:
				continue
			# 从完整路径中提取 S3 键
			s3_object_key = extract_s3_key_from_full_path(file.s3_url, s3_bucket_name)
			if not s3_object_key:
				_warning = f"S3 URL '{file.s3_url}' 的格式与预期的 's3://bucket_name/key' 不符或无效，跳过签名 URL 的生成。"
				logger.warning(_warning)
				frappe.msgprint(_warning, alert=True)
				continue
			try:
				# 生成预签名URL
				url = client.generate_presigned_url(
					"get_object",
					Params={"Bucket": s3_bucket_name, "Key": s3_object_key},
					ExpiresIn=3600,  # 1小时过期
				)
				file.signed_url = url
				file.signed_url_generated_at = now_datetime()
				updated = True
				logger.info(f"Generated signed URL for: {s3_object_key}")
			except Exception as e:
				logger.error(f"Error generating presigned URL for key '{s3_object_key}': {e}")
				file.signed_url = f"Error: {e!s}"
				updated = True
		if updated:
			doc.save(ignore_permissions=True)
			frappe.db.commit()
		return {"success": True}
	except Exception as e:
		logger.error(f"生成签名URL失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"生成签名URL失败: {e}"}
