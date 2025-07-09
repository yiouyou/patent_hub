import base64
import io
import mimetypes
import os

import frappe
import requests
from PIL import Image


def get_file_info(file_path):
	"""获取文件信息"""
	if not file_path:
		return None
	try:
		# 处理不同的文件路径格式
		if file_path.startswith("/files/"):
			# 去掉开头的 /files/
			relative_path = file_path[7:]
			full_path = frappe.get_site_path("public", "files", relative_path)
		elif file_path.startswith("/private/files/"):
			# 私有文件
			relative_path = file_path[15:]
			full_path = frappe.get_site_path("private", "files", relative_path)
		else:
			# 其他情况，尝试直接处理
			full_path = frappe.get_site_path("public", file_path.lstrip("/"))
		if not os.path.exists(full_path):
			frappe.log_error(f"文件不存在: {full_path}", "File Not Found")
			return None
		# 获取文件大小
		file_size = os.path.getsize(full_path)
		# 获取 MIME 类型
		mime_type, _ = mimetypes.guess_type(full_path)
		if not mime_type:
			mime_type = "application/octet-stream"
		return {
			"path": full_path,
			"size": file_size,
			"mime_type": mime_type,
			"name": os.path.basename(full_path),
		}
	except Exception as e:
		frappe.log_error(f"获取文件信息失败: {e!s}", "File Info Error")
		return None


def process_image(file_path, max_size=20 * 1024 * 1024):
	"""处理图片文件，确保符合 Anthropic 的要求"""
	try:
		with Image.open(file_path) as img:
			# 转换为 RGB 模式（如果需要）
			if img.mode in ("RGBA", "P"):
				img = img.convert("RGB")
			# 获取原始大小
			original_size = os.path.getsize(file_path)
			# 如果文件太大，需要压缩
			if original_size > max_size:
				# 计算压缩比例
				quality = 85
				output = io.BytesIO()
				while quality > 10:
					output.seek(0)
					output.truncate(0)
					img.save(output, format="JPEG", quality=quality, optimize=True)
					if output.tell() <= max_size:
						break
					quality -= 10
				# 如果仍然太大，尝试调整图片尺寸
				if output.tell() > max_size:
					img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
					output.seek(0)
					output.truncate(0)
					img.save(output, format="JPEG", quality=70, optimize=True)
				output.seek(0)
				return output.read(), "image/jpeg"
			else:
				# 文件大小合适，直接读取
				with open(file_path, "rb") as f:
					return f.read(), mimetypes.guess_type(file_path)[0] or "image/jpeg"
	except Exception as e:
		frappe.log_error(f"处理图片失败: {e!s}", "Image Processing Error")
		return None, None


def process_text_file(file_path, max_size=32 * 1024):
	"""处理文本文件"""
	try:
		with open(file_path, encoding="utf-8") as f:
			content = f.read()
		content_bytes = content.encode("utf-8")
		if len(content_bytes) > max_size:
			# 按字节截断，确保不会破坏UTF-8编码
			truncated_bytes = content_bytes[: max_size - 100]  # 留出空间给后缀
			content = truncated_bytes.decode("utf-8", errors="ignore") + "\n\n[文件内容已截断...]"
		return content
	except UnicodeDecodeError:
		# 如果不是 UTF-8，尝试其他编码
		try:
			with open(file_path, encoding="gbk") as f:
				content = f.read()
			content_bytes = content.encode("utf-8")
			if len(content_bytes) > max_size:
				truncated_bytes = content_bytes[: max_size - 100]
				content = truncated_bytes.decode("utf-8", errors="ignore") + "\n\n[文件内容已截断...]"
			return content
		except (UnicodeDecodeError, UnicodeEncodeError):
			frappe.log_error(f"文本文件编码错误: {file_path}", "Text Encoding Error")
			return None
	except Exception as e:
		frappe.log_error(f"处理文本文件失败: {e!s}", "Text Processing Error")
		return None


def create_content_block(prompt, attachment_path=None):
	"""创建内容块，支持文本和附件"""
	content = []
	# 处理附件
	if attachment_path:
		file_info = get_file_info(attachment_path)
		if file_info:
			mime_type = file_info["mime_type"]
			file_path = file_info["path"]
			file_name = file_info["name"]
			# 处理图片文件
			if mime_type.startswith("image/"):
				# Anthropic 支持的图片格式
				supported_image_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
				if mime_type in supported_image_types:
					image_data, processed_mime = process_image(file_path)
					if image_data:
						base64_data = base64.b64encode(image_data).decode("utf-8")
						content.append(
							{
								"type": "image",
								"source": {
									"type": "base64",
									"media_type": processed_mime,
									"data": base64_data,
								},
							}
						)
					else:
						content.append({"type": "text", "text": f"[无法处理图片文件: {file_name}]"})
				else:
					content.append({"type": "text", "text": f"[不支持的图片格式: {file_name} ({mime_type})]"})
			# 处理文本文件
			elif mime_type.startswith("text/") or mime_type == "application/json":
				text_content = process_text_file(file_path)
				if text_content:
					content.append(
						{"type": "text", "text": f"[附件内容 - {file_name}]:\n```\n{text_content}\n```"}
					)
				else:
					content.append({"type": "text", "text": f"[无法读取文件内容: {file_name}]"})
			# 其他类型文件
			else:
				content.append(
					{
						"type": "text",
						"text": f"[上传了文件: {file_name} ({mime_type})，但此文件类型暂不支持直接处理，请告诉我您希望如何处理这个文件]",
					}
				)
	# 添加文本内容
	if prompt:
		content.append({"type": "text", "text": prompt})
	return content


@frappe.whitelist()
def anthropic_call(
	prompt, attachment_path=None, model="claude-sonnet-4-20250514", max_tokens=8192, temperature=0.0
):
	"""
	调用 Anthropic API，支持文本和附件
	"""
	# 添加输入验证
	if not prompt and not attachment_path:
		frappe.throw("请提供消息内容或附件")
	if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 8192:
		frappe.throw("max_tokens 必须是1-8192之间的整数")
	if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 1:
		frappe.throw("temperature 必须在0-1之间")
	api_key = frappe.get_single("API KEY")
	ANTHROPIC_API_KEY = api_key.get_password("anthropic_api_key")
	if not ANTHROPIC_API_KEY:
		frappe.throw("Anthropic API Key 未配置")
	url = "https://api.anthropic.com/v1/messages"
	headers = {
		"x-api-key": ANTHROPIC_API_KEY,
		"Content-Type": "application/json",
		"anthropic-version": "2023-06-01",
	}
	# 创建内容块
	content = create_content_block(prompt, attachment_path)
	if not content:
		frappe.throw("请提供有效的消息内容或附件")
	data = {
		"model": model,
		"max_tokens": max_tokens,
		"temperature": temperature,
		"messages": [{"role": "user", "content": content}],
	}
	try:
		response = requests.post(url, json=data, headers=headers, timeout=60)
		if response.status_code == 200:
			result = response.json()
			content_blocks = result.get("content", [])
			if content_blocks and len(content_blocks) > 0:
				# 合并所有文本内容
				response_text = ""
				for block in content_blocks:
					if block.get("type") == "text":
						response_text += block.get("text", "")
				if response_text:
					return response_text
				else:
					frappe.throw("API 响应中没有文本内容")
			else:
				frappe.throw("API 响应格式异常，未找到内容")
		else:
			error_detail = ""
			try:
				error_response = response.json()
				error_detail = error_response.get("error", {}).get("message", response.text)
			except Exception:
				error_detail = response.text
			frappe.log_error(
				f"Anthropic API 错误响应: {response.status_code} - {error_detail}", "Anthropic API Error"
			)
			frappe.throw(f"Anthropic API 请求失败，状态码 {response.status_code}: {error_detail}")
	except requests.exceptions.Timeout:
		frappe.log_error("API 请求超时", "Anthropic API Timeout")
		frappe.throw("请求超时，请稍后重试")
	except requests.exceptions.ConnectionError as e:
		frappe.log_error(f"网络连接错误: {e!s}", "Network Connection Error")
		frappe.throw("网络连接失败，请检查网络设置")
	except requests.exceptions.RequestException as e:
		frappe.log_error(f"网络请求异常: {e!s}", "Network Request Error")
		frappe.throw(f"网络请求异常: {e!s}")
	except Exception as e:
		frappe.log_error(f"调用 Anthropic API 时发生错误: {e!s}", "API Error")
		frappe.throw(f"调用 Anthropic API 时发生错误: {e!s}")
