import json
import logging
import subprocess
import time
from datetime import datetime, timezone

import frappe
import requests
from aliyunsdkcore.client import AcsClient
from aliyunsdkecs.request.v20140526.DescribeInstancesRequest import DescribeInstancesRequest
from aliyunsdkecs.request.v20140526.RunInstancesRequest import RunInstancesRequest

logger = frappe.logger("app.patent_hub.patent_wf._ali_spot")
logger.setLevel(logging.INFO)

ALIYUN_CONFIG = {
	"region": "us-west-1",
	"zone_id": "us-west-1b",
	"image_id": "m-rj9fgzzfoe0robnldsap",
	"instance_type": [
		"ecs.n1.medium",
		"ecs.sn1.medium",
		"ecs.e-c1m2.large",
		"ecs.n4.large",
		"ecs.c8y.large",
		"ecs.u1-c1m2.large",
	],
	"security_group": "sg-rj983e9wauefg6dpluvu",
	"vswitch_id": "vsw-rj98hw33pyjzp3x8midvi",
	"key_pair": "ali-us",
}

PING_TIMEOUT_SECS = 2
WAIT_IP_RETRIES = 24  # 最长等 24 * 5 = 120 秒
WAIT_IP_DELAY_SECS = 5


@frappe.whitelist()
def ping(server_ip_port):
	try:
		url = f"{server_ip_port}/docs"
		resp = requests.get(url, timeout=PING_TIMEOUT_SECS)
		logger.info(url)
		logger.info(resp)
		return resp.status_code == 200 and bool(resp.text.strip())
	except Exception:
		return False


@frappe.whitelist()
def check_spot_status():
	doc = frappe.get_single("API Endpoint")
	server_ip_port = doc.server_ip_port

	if not server_ip_port:
		logger.info("未配置 server_ip_port，跳过状态检查。")
		return

	doc.spot_status = "On" if ping(server_ip_port) else "Off"
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def wait_for_public_ip(client, instance_id, retries=WAIT_IP_RETRIES, delay=WAIT_IP_DELAY_SECS):
	describe = DescribeInstancesRequest()
	describe.set_accept_format("json")
	describe.set_InstanceIds(json.dumps([instance_id]))

	for _ in range(retries):
		time.sleep(delay)
		info = json.loads(client.do_action_with_exception(describe))
		instances = info.get("Instances", {}).get("Instance", [])
		if instances and instances[0].get("PublicIpAddress", {}).get("IpAddress"):
			return instances[0]["PublicIpAddress"]["IpAddress"][0]
	return None


def _try_launch_with_type(client, instance_type):
	"""
	只负责以指定规格创建 Spot 实例并返回实例 ID。
	- 创建失败：抛异常（由调用方决定是否继续尝试其它规格）。
	- 创建成功：返回 instance_id（调用方随后等待公网 IP，不再尝试其它规格）。
	"""
	request = RunInstancesRequest()
	request.set_accept_format("json")
	request.set_InstanceType(instance_type)
	request.set_ImageId(ALIYUN_CONFIG["image_id"])
	request.set_SecurityGroupId(ALIYUN_CONFIG["security_group"])
	request.set_KeyPairName(ALIYUN_CONFIG["key_pair"])
	request.set_VSwitchId(ALIYUN_CONFIG["vswitch_id"])
	request.set_ZoneId(ALIYUN_CONFIG["zone_id"])
	request.set_InternetMaxBandwidthOut(10)
	request.set_InternetChargeType("PayByTraffic")
	request.set_InstanceChargeType("PostPaid")
	request.set_SpotStrategy("SpotAsPriceGo")

	# 显式限定数量，避免多开
	request.add_query_param("Amount", 1)
	request.add_query_param("MinAmount", 1)
	request.add_query_param("MaxAmount", 1)

	# 幂等：避免重复提交导致多开
	client_token = f"spot-{instance_type}-{int(time.time())}"
	request.add_query_param("ClientToken", client_token)

	# 系统盘
	request.set_SystemDisk({"Category": "cloud_essd_entry", "Size": 20, "PerformanceLevel": "PL0"})

	# 实例名：spot-YYYYMMDD-HHMM-<type>
	name_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
	request.set_InstanceName(f"spot-{name_stamp}-{instance_type}")

	logger.info(f"尝试以规格 {instance_type} 启动实例...")
	response = client.do_action_with_exception(request)
	instance_info = json.loads(response)
	instance_ids = (
		instance_info.get("InstanceIdSets", {}).get("InstanceIdSet")
		or instance_info.get("InstanceIdList")
		or []
	)
	if not instance_ids:
		raise RuntimeError(f"未获取到 InstanceId，返回内容：{instance_info}")
	instance_id = instance_ids[0]

	logger.info(f"规格 {instance_type} 创建成功，InstanceId={instance_id}")
	return instance_id


@frappe.whitelist()
def run(docname):
	doc = frappe.get_doc("API Endpoint", docname)

	api_endpoint = frappe.get_single("API KEY")
	ALI_ACCESS_KEY = api_endpoint.get_password("ali_accesskey_id")
	ALI_ACCESS_SECRET = api_endpoint.get_password("ali_accesskey_secret")

	# 初始化客户端（如果这里失败，直接抛出初始化失败，避免后续逻辑误判）
	try:
		client = AcsClient(ALI_ACCESS_KEY, ALI_ACCESS_SECRET, ALIYUN_CONFIG["region"])
	except Exception:
		logger.error("初始化 Aliyun 客户端失败：\n" + frappe.get_traceback())
		frappe.throw("启动失败，请查看错误日志")

	errors = []

	# 按顺序尝试创建；一旦“创建成功”，就停止继续尝试其它规格
	chosen_instance_id = None
	chosen_type = None

	for itype in ALIYUN_CONFIG["instance_type"]:
		try:
			instance_id = _try_launch_with_type(client, itype)
			chosen_instance_id = instance_id
			chosen_type = itype
			break  # ✅ 成功创建后不再尝试其它规格
		except Exception as e:
			msg = str(e) or e.__class__.__name__
			errors.append(f"{itype} -> {msg}")
			logger.error(f"规格 {itype} 启动失败：{msg}\n" + frappe.get_traceback())
			continue

	# 如果没有任何规格创建成功，统一抛错
	if not chosen_instance_id:
		detail = "\n".join(f"- {line}" for line in errors) if errors else "- 未产生可用的错误信息"
		logger.error("启动 Aliyun Spot 实例失败（已尝试所有规格）：\n" + detail)
		frappe.throw("启动失败，请查看错误日志\n" + detail)

	# 仅对该实例等待公网 IP，不再创建其它实例
	logger.info(f"开始等待实例 {chosen_instance_id} 分配公网 IP（规格 {chosen_type}）...")
	ip = wait_for_public_ip(client, chosen_instance_id, retries=WAIT_IP_RETRIES, delay=WAIT_IP_DELAY_SECS)

	if not ip:
		# 为避免误开多个实例，这里不再继续创建其它规格
		logger.error(f"实例 {chosen_instance_id}（规格 {chosen_type}）未在预期时间内获取到公网 IP")
		frappe.throw(
			f"实例已创建（{chosen_type}），但未在预期时间内获取到公网 IP，请稍后在控制台检查实例状态或释放后重试。"
			f"InstanceId={chosen_instance_id}"
		)

	# 成功：保存并返回
	doc.server_ip_port = f"http://{ip}:28285"
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	logger.info(f"规格 {chosen_type} 启动成功，地址：{doc.server_ip_port}，InstanceId={chosen_instance_id}")
	return doc.server_ip_port
