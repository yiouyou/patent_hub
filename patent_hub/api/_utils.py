import base64
import gzip
import json
from typing import Any


# 🔹 文本压缩 / 解压
def compress_str_to_base64(text: str) -> str:
	"""压缩字符串并转为 base64 编码"""
	compressed = gzip.compress(text.encode("utf-8"))
	return base64.b64encode(compressed).decode("utf-8")


def decompress_str_from_base64(base64_str: str) -> str:
	"""解压 base64 编码的压缩字符串"""
	compressed = base64.b64decode(base64_str.encode("utf-8"))
	return gzip.decompress(compressed).decode("utf-8")


# 🔹 JSON 对象压缩 / 解压（list、dict）
def compress_json_to_base64(obj: Any) -> str:
	"""将 Python 对象压缩并 base64 编码"""
	json_str = json.dumps(obj)
	return compress_str_to_base64(json_str)


def decompress_json_from_base64(base64_str: str) -> Any:
	"""解压后还原为 Python 对象"""
	json_str = decompress_str_from_base64(base64_str)
	return json.loads(json_str)


# 🔹 文件压缩 / 解压
def compress_file_to_base64(path: str) -> str:
	"""将文件压缩后转为 base64 编码字符串"""
	with open(path, "rb") as f:
		data = f.read()
	compressed = gzip.compress(data)
	return base64.b64encode(compressed).decode("utf-8")


def decompress_file_from_base64(base64_str: str, save_path: str):
	"""将 base64 压缩内容解压并保存为文件"""
	compressed = base64.b64decode(base64_str.encode("utf-8"))
	data = gzip.decompress(compressed)
	with open(save_path, "wb") as f:
		f.write(data)


from frappe.model.naming import make_autoname


def generate_step_id(patent_id: str, prefix: str) -> str:
	"""
	使用 Frappe 的 make_autoname 生成 {patent_id}-{prefix}-.#
	"""
	return make_autoname(f"{patent_id}-{prefix}-.#")
