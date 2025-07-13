import logging

import boto3
import frappe
from frappe.utils import add_to_date, now_datetime

logger = frappe.logger("app_patent_hub")
logger.setLevel(logging.INFO)


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
def generate_signed_urls(doclabel: str, docname: str):
	"""为上传的文件生成签名URL"""
	try:
		doc = frappe.get_doc(doclabel, docname)
		# 检查是否有任何 s3_url 存在
		has_s3_urls = any(file.s3_url for file in doc.generated_files)
		if not has_s3_urls:
			return {"success": True, "message": "没有 S3 文件需要生成签名URL"}
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
			# 跳过没有 s3_url 的记录
			if not file.s3_url:
				continue
			# 检查是否需要重新生成签名URL（如果超过1小时或没有生成过）
			if file.signed_url_generated_at:
				if now_datetime() < add_to_date(file.signed_url_generated_at, hours=1):
					continue  # 已生成，未过期，跳过
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
