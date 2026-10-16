# -*- coding: utf-8 -*-
"""
MES3.0 接口压力测试脚本（Locust）

覆盖接口（全部为 POST + application/json）：

  #  接口                                       后端控制器
  1  POST /PassStation/Index                     MES.PACK.PassStation/Controllers/PassStation/CustomPassStationController.cs -> Index
  2  POST /SMT/PassStation                       MES.SMT/Controllers/PassStation/PassStationController.cs -> Index
  3  POST /WMS/BeiLiaoChoose/Command/Check       MES.WMS/Controllers/BeiLiaoChoose/CommandController.cs -> CheckAsync
  4  POST /WMS/BinLocationMaterial               MES.WMS/Controllers/PDA/BinLocationMaterialController.cs -> Index

鉴权说明（来自 BaseCommon/Common/GlobalFilters.cs 的 AuthorizeMiddleware + GlobalActionFilter）：
  * 所有请求都必须带 AppID(11位) 与 AppSecret(10位) 请求头，否则中间件直接返回“无法识别客户端”。
  * 接口 2/3/4 未标 [NoIdleBus]，因此 GlobalActionFilter 会强制校验 token 与 CustomerID（分库因素）；
    接口 1 标了 [NoIdleBus]，配合 [AllowAnonymous] 可免 token。
  * 请求头 ClientID=jmeter 时，后端会跳过“请求流水”落库（见 GlobalFilters 第 78/512/649 行），
    压测时强烈建议开启，避免日志写入成为瓶颈、污染业务库。

运行方式见同目录 README.md。
"""

from __future__ import annotations

import logging
import os
import random

from locust import HttpUser, between, events, task

logger = logging.getLogger("mes-locust")


# ==========================================================================
# 配置区（全部可用环境变量覆盖，便于本地 / k8s 复用同一份脚本）
# ==========================================================================
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


# --- 目标服务（--host 优先级最高；否则读 LOCUST_HOST / MES_BASE_URL） --------
DEFAULT_HOST = _env("LOCUST_HOST") or _env("MES_BASE_URL") or "http://127.0.0.1:7009"

# --- 统一鉴权头（取自 mes3-web/.env 与 common-webpack/common/http.js） -------
APP_ID = _env("MES_APP_ID", "v2NDIyMTkzD")        # 必须 11 位
APP_SECRET = _env("MES_APP_SECRET", "YWRtaW4iLC")  # 必须 10 位
SYSTEM_ID = _env("MES_SYSTEM_ID", "MES")
LANG_ID = _env("MES_LANG_ID", "zh-CN")
CUSTOMER_ID = _env("MES_CUSTOMER_ID", "")          # 分库因素，接口 2/3/4 必填（留空则登录后自动取第一个授权客户）
ORGANIZE_ID = _env("MES_ORGANIZE_ID", "")          # 工场，留空则登录后自动取
USER_ID = _env("MES_USER_ID", "")                  # 业务入参里的操作人

# --- 登录获取 token ---------------------------------------------------------
USERNAME = _env("MES_USERNAME")
PASSWORD = _env("MES_PASSWORD")
STATIC_TOKEN = _env("MES_TOKEN")                   # 已手工准备好 token 时直接填，可跳过登录
LOGIN_IN_ONSTART = _env_bool("MES_LOGIN_IN_ONSTART", True)

# --- 压测行为开关 -----------------------------------------------------------
SKIP_REQUEST_LOG = _env_bool("MES_SKIP_REQUEST_LOG", True)     # 加 ClientID=jmeter 跳过服务端请求日志
STRICT_MODE = _env_bool("MES_STRICT", False)                    # True 时业务码非 200 一律判失败
PASSSTATION_IS_QUERY = _env_bool("MES_PASSSTATION_IS_QUERY", True)  # 接口 1 只校验不落库（压测安全）

# --- 请求任务权重 -----------------------------------------------------------
W_PASSSTATION_INDEX = _env_int("MES_W_PS_INDEX", 3)
W_SMT_PASSSTATION = _env_int("MES_W_SMT_PASSSTATION", 3)
W_BEILIAO_CHECK = _env_int("MES_W_BEILIAO_CHECK", 2)
W_BIN_LOCATION_MATERIAL = _env_int("MES_W_BIN_LOCATION", 2)

# --- 模拟用户思考时间 -------------------------------------------------------
WAIT_MIN = float(_env("MES_WAIT_MIN", "1"))
WAIT_MAX = float(_env("MES_WAIT_MAX", "3"))

# --- 业务数据（务必换成压测环境的真实数据，否则业务校验会返回 code=400） ----
PS_SN = _env("MES_PS_SN", "DEMO-SN-0001")                    # 追溯码
PS_STATION_ID = _env("MES_PS_STATION_ID", "ST01")            # 工序编号
PS_FACTORY_ID = _env("MES_PS_FACTORY_ID", "")
PS_LINE_ID = _env("MES_PS_LINE_ID", "")

SMT_LOT_NO = _env("MES_SMT_LOT_NO", "DEMO-LOT-0001")         # 内部工单号
SMT_PROCESSOR_CODE = _env("MES_SMT_PROCESSOR_CODE", "PCBTrace")
SMT_ACTION = _env("MES_SMT_ACTION", "QueryInsideLotListInfo")  # 只读查询，避免产生过站数据

WMS_FALIAO_NO = _env("MES_WMS_FALIAO_NO", "DEMO-FL-0001")    # 发料单/备料单号
WMS_REEL_ID = _env("MES_WMS_REEL_ID", "DEMO-REEL-0001")      # 料盘号
WMS_TAO_LIANG = _env("MES_WMS_TAO_LIANG", "")
WMS_SHIFT = _env("MES_WMS_SHIFT", "白班")

WMS_POSITION = _env("MES_WMS_POSITION", "A-01-01")           # 仓位号
WMS_MAT_CODE = _env("MES_WMS_MAT_CODE", "DEMO-MAT-001")      # 物料编码
WMS_WAREHOUSE_ID = _env("MES_WMS_WAREHOUSE_ID", "")
WMS_BIN_ACTION = _env("MES_WMS_BIN_ACTION", "Query")         # Query=只读；Add=写入，压测慎用

# 业务码：200=成功；400=业务校验未通过（如查无数据）；410=登录过期；500=服务端异常
OK_CODES = {200} if STRICT_MODE else {200, 400}


def _rand_suffix(n: int = 4) -> str:
    return "".join(random.choice("0123456789") for _ in range(n))


class MESUser(HttpUser):
    """MES3.0 接口压测用户。

    每个虚拟用户在 on_start 时登录一次拿到 token（也可通过 MES_TOKEN 预置），
    随后按权重随机调用 4 个接口。
    """

    host = DEFAULT_HOST
    wait_time = between(WAIT_MIN, WAIT_MAX)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        self.token: str = ""
        self.customer_id: str = CUSTOMER_ID
        self.organize_id: str = ORGANIZE_ID

        if STATIC_TOKEN:
            self.token = STATIC_TOKEN
            logger.info("使用预置 token（MES_TOKEN）")
        elif LOGIN_IN_ONSTART and USERNAME:
            self._login()
        else:
            logger.warning(
                "未配置 MES_USERNAME/MES_TOKEN：接口 2/3/4 会因缺少 token 被鉴权拦截（code=410）"
            )

        self.headers = self._build_headers()

    # ------------------------------------------------------------------
    # 请求头
    # ------------------------------------------------------------------
    def _build_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "AppID": APP_ID,
            "AppSecret": APP_SECRET,
            "SystemID": SYSTEM_ID,
            "LangID": LANG_ID,
        }
        if USER_ID:
            headers["UserId"] = USER_ID
        if self.token:
            headers["token"] = self.token
        if self.customer_id:
            headers["CustomerID"] = self.customer_id
        if self.organize_id:
            headers["OrganizeID"] = self.organize_id
        if SKIP_REQUEST_LOG:
            headers["ClientID"] = "jmeter"
        return headers

    # ------------------------------------------------------------------
    # 登录并解析 token / 客户 / 工场
    # ------------------------------------------------------------------
    def _login(self) -> None:
        payload = {"username": USERNAME, "password": PASSWORD}
        with self.client.post(
            "/Auth/Login",
            json=payload,
            headers=self._build_headers(),
            name="POST /Auth/Login",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"登录 HTTP {resp.status_code}")
                return
            try:
                body = resp.json()
            except Exception:
                resp.failure("登录响应不是合法 JSON")
                return

            if body.get("code") != 200:
                resp.failure(f"登录失败：{body.get('info')}")
                return

            token_data = body.get("TokenData") or {}
            token = (token_data.get("token") or "").strip()
            if not token:
                resp.failure("登录成功但未返回 TokenData.token")
                return

            self.token = token
            if not self.organize_id:
                self.organize_id = (token_data.get("OrganizeId") or "").strip()

            # 分库客户：优先取用户首个授权客户
            if not self.customer_id:
                models = body.get("modelData") or []
                if isinstance(models, list) and models:
                    self.customer_id = (models[0].get("CustomerId") or "").strip()

            resp.success()

    # ------------------------------------------------------------------
    # 统一 POST + 业务码校验
    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict, name: str) -> None:
        with self.client.post(
            path, json=payload, headers=self.headers, name=name, catch_response=True
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            try:
                body = resp.json()
            except Exception:
                resp.failure("响应不是合法 JSON")
                return

            code = body.get("code")
            if code in OK_CODES:
                resp.success()
            elif code in (410,):
                resp.failure(f"登录失效(code={code})：{body.get('info')}")
            else:
                resp.failure(f"业务异常(code={code})：{body.get('info')}")

    # ==================================================================
    # 任务 1：POST /PassStation/Index —— PACK 标准过站
    #   Body: SNStatusInput（BaseModels/MES.PACK.PassStation/Dtos/SNStatusInput.cs）
    #   默认 IsQuery=True，只做校验不写库，避免压测产生过站数据
    # ==================================================================
    @task(W_PASSSTATION_INDEX)
    def pass_station_index(self) -> None:
        payload = {
            "SN": PS_SN,
            "StationId": PS_STATION_ID,
            "CustomerId": self.customer_id,
            "UserId": USER_ID,
            "FactoryId": PS_FACTORY_ID,
            "LineId": PS_LINE_ID,
            "HostName": "locust-load-test",
            "PassStationType": 0,          # 0-自动 1-进站 2-出站
            "Result": 1,                   # 1-通过 0-不良
            "IsQuery": PASSSTATION_IS_QUERY,
            "AutoInsertTestData": False,
            "Description": "locust-load-test",
        }
        self._post("/PassStation/Index", payload, "POST /PassStation/Index")

    # ==================================================================
    # 任务 2：POST /SMT/PassStation —— SMT 工序过站 / 查询（Action 驱动）
    #   Body: PassStationDto（MES.SMT/Dto/ProcessHandle/PassStationDto.cs）
    # ==================================================================
    @task(W_SMT_PASSSTATION)
    def smt_pass_station(self) -> None:
        payload = {
            "Action": SMT_ACTION,          # 默认 QueryInsideLotListInfo（只读）
            "OperateMark": "select",
            "ProcessorCode": SMT_PROCESSOR_CODE,
            "LotNo": SMT_LOT_NO,
            "StationId": "",
            "UserId": USER_ID,
            "CustomerId": self.customer_id,
            "PageIndex": 1,
            "PageSize": 30,
        }
        self._post("/SMT/PassStation", payload, "POST /SMT/PassStation")

    # ==================================================================
    # 任务 3：POST /WMS/BeiLiaoChoose/Command/Check —— 备料单核对
    #   Body: MatQueryInput（MES.WMS/Services/BeiLiaoChoose/Dtos/MatQueryInput.cs）
    # ==================================================================
    @task(W_BEILIAO_CHECK)
    def beiliao_choose_check(self) -> None:
        payload = {
            "OperateMark": "select",
            "Action": "Check",
            "Number": WMS_FALIAO_NO,
            "QrFaLiaoNo": WMS_FALIAO_NO,
            "BeiLiaoNo": "",
            "ReelId": WMS_REEL_ID,
            "OnlyCheckMaterial": False,
            "Shift": WMS_SHIFT,
            "TaoLiang": WMS_TAO_LIANG,
            "UserId": USER_ID,
            "CustomerId": self.customer_id,
        }
        self._post(
            "/WMS/BeiLiaoChoose/Command/Check",
            payload,
            "POST /WMS/BeiLiaoChoose/Command/Check",
        )

    # ==================================================================
    # 任务 4：POST /WMS/BinLocationMaterial —— 仓位与物料关联（Action 驱动）
    #   Body: BinLocationMaterialDto（MES.WMS/Dto/PDA/BinLocationMaterialDto.cs）
    # ==================================================================
    @task(W_BIN_LOCATION_MATERIAL)
    def bin_location_material(self) -> None:
        payload = {
            "Action": WMS_BIN_ACTION,      # 默认 Query（只读）；Add 会写库
            "OperateMark": "select",
            "Position": WMS_POSITION,
            "MatCode": WMS_MAT_CODE,
            "FBoxOrReelId": WMS_REEL_ID,
            "WarehouseId": WMS_WAREHOUSE_ID,
            "UserId": USER_ID,
            "CustomerId": self.customer_id,
        }
        self._post("/WMS/BinLocationMaterial", payload, "POST /WMS/BinLocationMaterial")


@events.test_start.add_listener
def _on_test_start(environment, **_kwargs) -> None:
    logger.info(
        "压测启动 -> host=%s | AppID=%s | SystemID=%s | CustomerID=%s | "
        "SkipRequestLog=%s | PassStationIsQuery=%s | Strict=%s",
        environment.host or DEFAULT_HOST,
        APP_ID,
        SYSTEM_ID,
        CUSTOMER_ID or "<登录后自动获取>",
        SKIP_REQUEST_LOG,
        PASSSTATION_IS_QUERY,
        STRICT_MODE,
    )
