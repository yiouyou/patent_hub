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

# ... 保持现有的 run() 和 _job() 函数不变 ...


def extract_s3_key_from_full_path(s3_full_path: str, bucket_name: str) -> str:
	"""
	Extracts the S3 object key from a full S3 path given the bucket name.
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


def classify_file_type(s3_url: str) -> str:
	"""
	根据 S3 URL 分类文件类型
	Args:
		s3_url: S3 文件路径
	Returns:
		文件类型: 'final_markdown', 'final_docx', 'other_docx', 'unknown'
	"""
	if not s3_url:
		return "unknown"
	# 检查是否是 final_markdown (以 "c2d/input_text.txt" 结尾)
	if s3_url.endswith("c2d/input_text.txt"):
		return "final_markdown"
	# 检查是否是 docx 文件
	if s3_url.endswith(".docx") and "c2d/" in s3_url:
		# 获取文件名
		filename = s3_url.split("/")[-1]
		# 排除的 docx 文件
		excluded_docx = ["abstract.docx", "claims.docx", "description.docx", "figures.docx"]
		if filename in excluded_docx:
			return "other_docx"
		else:
			return "final_docx"
	return "unknown"


@frappe.whitelist()
def generate_signed_urls(docname: str):
	doc = frappe.get_doc("Claims To Docx", docname)
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
	final_markdown_url = None
	final_docx_url = None
	for file in doc.generated_files:
		if file.signed_url_generated_at:
			if now_datetime() < add_to_date(file.signed_url_generated_at, hours=1):
				# 已生成，未过期，但仍需检查是否是目标文件
				file_type = classify_file_type(file.s3_url)
				if file_type == "final_markdown":
					final_markdown_url = file.signed_url
				elif file_type == "final_docx":
					final_docx_url = file.signed_url
				continue  # 跳过重新生成
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
			# 检查文件类型并保存对应的URL
			file_type = classify_file_type(file.s3_url)
			if file_type == "final_markdown":
				final_markdown_url = url
			elif file_type == "final_docx":
				final_docx_url = url
		except Exception as e:
			logger.error(f"Error generating presigned URL for key '{s3_object_key}': {e}")
			# Optionally, you can set an error message in file.signed_url or another field
			file.signed_url = f"Error: {e!s}"
			updated = True  # Mark as updated to save the error status
	# 更新主文档的按钮字段
	if final_markdown_url:
		doc.final_markdown = final_markdown_url
	if final_docx_url:
		doc.final_docx = final_docx_url
	if updated or final_markdown_url or final_docx_url:
		doc.save(ignore_permissions=True)
	return {
		"success": True,
		"final_markdown_available": bool(final_markdown_url),
		"final_docx_available": bool(final_docx_url),
	}


@frappe.whitelist()
def get_download_info(docname: str):
	"""
	获取下载信息，包括文件可用性和链接状态
	"""
	doc = frappe.get_doc("Claims To Docx", docname)
	files = doc.generated_files or []
	now = now_datetime()
	result = {
		"final_markdown": {"available": False, "expired": True, "url": None},
		"final_docx": {"available": False, "expired": True, "url": None},
	}
	for file in files:
		if not file.s3_url or not file.signed_url:
			continue
		file_type = classify_file_type(file.s3_url)
		if file_type in ["final_markdown", "final_docx"]:
			# 检查链接是否过期
			expired = True
			if file.signed_url_generated_at:
				expired = now > add_to_date(file.signed_url_generated_at, hours=1)
			key = file_type
			result[key] = {
				"available": True,
				"expired": expired,
				"url": file.signed_url if not expired else None,
			}
	return result
