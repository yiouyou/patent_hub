import frappe
import requests


@frappe.whitelist()
def anthropic_call(prompt, model="claude-sonnet-4-20250514", max_tokens=1000, temperature=0.7):
	"""
	调用 Anthropic API
	"""
	api_key = frappe.get_doc("API KEY")
	ANTHROPIC_API_KEY = api_key.ANTHROPIC_API_KEY
	if not ANTHROPIC_API_KEY:
		frappe.throw("Anthropic API Key 未配置")
	url = "https://api.anthropic.com/v1/messages"
	headers = {
		"x-api-key": ANTHROPIC_API_KEY,
		"Content-Type": "application/json",
		"anthropic-version": "2023-06-01",
	}
	data = {
		"model": model,
		"max_tokens": max_tokens,
		"temperature": temperature,
		"messages": [{"role": "user", "content": prompt}],
	}
	try:
		response = requests.post(url, json=data, headers=headers, timeout=30)
		if response.status_code == 200:
			result = response.json()
			content = result.get("content", [])
			if content and len(content) > 0:
				response_text = content[0].get("text", "")
				# 添加调试日志
				frappe.log_error(f"API Response: {response_text}", "Anthropic API Debug")
				return response_text  # 直接返回字符串
			else:
				frappe.throw("API 响应格式异常，未找到内容")
		else:
			error_detail = ""
			try:
				error_response = response.json()
				error_detail = error_response.get("error", {}).get("message", response.text)
			except:
				error_detail = response.text
			frappe.throw(f"Anthropic API 请求失败，状态码 {response.status_code}: {error_detail}")
	except requests.exceptions.RequestException as e:
		frappe.throw(f"网络请求异常: {e!s}")
	except Exception as e:
		frappe.throw(f"调用 Anthropic API 时发生错误: {e!s}")
