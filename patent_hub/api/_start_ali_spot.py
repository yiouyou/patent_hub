import json
import logging
import subprocess
import time
from datetime import datetime, timezone

import frappe
from aliyunsdkcore.client import AcsClient
from aliyunsdkecs.request.v20140526.DescribeInstancesRequest import DescribeInstancesRequest
from aliyunsdkecs.request.v20140526.RunInstancesRequest import RunInstancesRequest

logger = frappe.logger("app.patent_hub.patent_wf._start_ali_spot")
logger.setLevel(logging.DEBUG)

ALIYUN_CONFIG = {
	"region": "us-west-1",
	"zone_id": "us-west-1b",
	"image_id": "m-rj9bb8qrfdwf0ajmnzsj",
	"instance_type": "ecs.sn1.medium",  # ecs.sn1.medium
	"security_group": "sg-rj983e9wauefg6dpluvu",
	"vswitch_id": "vsw-rj98hw33pyjzp3x8midvi",
	"key_pair": "ali-us",
}


@frappe.whitelist()
def ping(host):
	try:
		result = subprocess.run(
			["ping", "-c", "1", "-W", "2", host], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
		)
		return result.returncode == 0
	except Exception:
		return False


def wait_for_public_ip(client, instance_id, retries=10, delay=5):
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


@frappe.whitelist()
def run(docname):
	doc = frappe.get_doc("API Endpoint", docname)

	ACCESS_KEY = doc.accesskey_id
	ACCESS_SECRET = doc.accesskey_secret

	try:
		client = AcsClient(ACCESS_KEY, ACCESS_SECRET, ALIYUN_CONFIG["region"])

		request = RunInstancesRequest()
		request.set_accept_format("json")
		request.set_InstanceType(ALIYUN_CONFIG["instance_type"])
		request.set_ImageId(ALIYUN_CONFIG["image_id"])
		request.set_SecurityGroupId(ALIYUN_CONFIG["security_group"])
		request.set_KeyPairName(ALIYUN_CONFIG["key_pair"])
		request.set_VSwitchId(ALIYUN_CONFIG["vswitch_id"])
		request.set_ZoneId(ALIYUN_CONFIG["zone_id"])
		request.set_InternetMaxBandwidthOut(10)
		request.set_InternetChargeType("PayByTraffic")
		request.set_InstanceChargeType("PostPaid")
		request.set_SpotStrategy("SpotAsPriceGo")
		request.set_MinAmount(1)
		request.set_SystemDisk({"Category": "cloud_essd_entry", "Size": 20, "PerformanceLevel": "PL0"})
		# 🎯 设置实例名称为 spot-YYYYMMDD-HHMM
		name_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
		request.set_InstanceName(f"spot-{name_stamp}")

		response = client.do_action_with_exception(request)
		instance_info = json.loads(response)
		instance_id = instance_info["InstanceIdSets"]["InstanceIdSet"][0]

		time.sleep(10)
		ip = wait_for_public_ip(client, instance_id)

		if not ip:
			frappe.throw("实例已启动，但未获取到公网 IP，请稍后手动确认")

		doc.server_ip_port = f"http://{ip}:28285"
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		return doc.server_ip_port

	except Exception:
		logger.error("启动 Aliyun Spot 实例失败:\n" + frappe.get_traceback())
		frappe.throw("启动失败，请查看错误日志")
