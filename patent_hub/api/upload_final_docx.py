import base64
import logging
import os
import re
import tempfile
import unicodedata
import urllib.parse
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
		if not doc.final_markdown and not doc.final_docx:
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
		s3_prefix = f"{_title}/ufd"
		uploaded_files = []
		# 上传 Final Markdown
		if doc.final_markdown:
			try:
				file_name = doc.final_markdown.split("/")[-1]
				file_path = frappe.get_site_path("private", "files", file_name)
				if not os.path.exists(file_path):
					raise FileNotFoundError(f"文件未找到: {file_path}")
				logger.info(f"找到文件路径: {file_path}")
				# 读取文件内容
				with open(file_path, "rb") as f:
					file_content = f.read()
				# 确定文件扩展名
				s3_key = f"{s3_prefix}/{timestamp}.txt"
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
		if doc.final_docx:
			try:
				file_name = doc.final_docx.split("/")[-1]
				file_path = frappe.get_site_path("private", "files", file_name)
				if not os.path.exists(file_path):
					raise FileNotFoundError(f"文件未找到: {file_path}")
				logger.info(f"找到文件路径: {file_path}")
				# 读取文件内容
				with open(file_path, "rb") as f:
					file_content = f.read()
				s3_key = f"{s3_prefix}/{timestamp}.docx"
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
			logger.info(f"文档 {docname} 上传完成")
		return {"success": True, "message": f"成功上传 {len(uploaded_files)} 个文件"}
	except Exception as e:
		logger.error(f"上传文件失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"上传文件失败: {e}"}
