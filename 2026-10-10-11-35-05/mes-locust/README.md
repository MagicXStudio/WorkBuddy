# MES3.0 接口压力测试（Locust）

针对以下 4 个接口的 Locust 压测脚本，接口定义均从后端源码中核对确认。

## 一、接口清单（源码核对结论）

| # | 接口（POST，JSON） | 后端控制器 / 文件 | 请求体 DTO | token | AppID/Secret | CustomerID(分库) |
|---|---|---|---|---|---|---|
| 1 | `/PassStation/Index` | `MES.PACK.PassStation/Controllers/PassStation/CustomPassStationController.cs` → `Index` | `SNStatusInput` | 否¹ | 是 | 否¹ |
| 2 | `/SMT/PassStation` | `MES.SMT/Controllers/PassStation/PassStationController.cs` → `Index` | `PassStationDto` | **是** | 是 | **是** |
| 3 | `/WMS/BeiLiaoChoose/Command/Check` | `MES.WMS/Controllers/BeiLiaoChoose/CommandController.cs` → `CheckAsync` | `MatQueryInput` | **是** | 是 | **是** |
| 4 | `/WMS/BinLocationMaterial` | `MES.WMS/Controllers/PDA/BinLocationMaterialController.cs` → `Index` | `BinLocationMaterialDto` | **是** | 是 | **是** |

¹ 接口 1 的 Action 标注了 `[NoIdleBus] + [AllowAnonymous]`，因此可免 token 与分库校验（但中间件仍要求 AppID/AppSecret）。

> 鉴权结论来自 `BaseCommon/Common/GlobalFilters.cs`：接口 2/3/4 未标 `[NoIdleBus]`，`GlobalActionFilter` 会执行
> `if (!NoNeedCheckOperate || !NoNeedIdleBus) NoNeedCheckToken = false;`，即强制校验 token，进而校验 CustomerID 分库。
> 所以脚本在 `on_start` 里登录一次拿 token，并从登录响应自动带出 `CustomerId` / `OrganizeId`。

另外发现后端有一个**对压测专门友好的开关**：请求头 `ClientID: jmeter` 时跳过“请求流水”落库
（`GlobalFilters.cs` 第 78、512、649 行）。脚本默认开启（`MES_SKIP_REQUEST_LOG=true`），避免日志写库成为瓶颈。

## 二、快速开始

```bash
cd mes-locust
python -m venv .venv && . .venv/Scripts/activate     # Windows Git Bash
pip install -r requirements.txt

# 复制并按压测环境修改（重点：MES_USERNAME / MES_PASSWORD / MES_CUSTOMER_ID / 业务数据）
cp .env.example .env
set -a && source .env && set +a

# Web UI 模式（浏览器打开 http://localhost:8089）
locust -f locustfile.py --host http://127.0.0.1:7009

# 无界面压测：200 并发，每秒起 20 个用户，跑 5 分钟，并输出 HTML 报告
locust -f locustfile.py --headless -u 200 -r 20 -t 5m \
  --host http://127.0.0.1:7009 --html report.html --csv result
```

只压某几个接口时用 tag 过滤（tag 名为接口短名）：

```bash
locust -f locustfile.py --tags WMS --headless -u 100 -r 10 -t 3m --host http://127.0.0.1:7009
```

## 三、环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `LOCUST_HOST` / `MES_BASE_URL` | 目标服务地址（`--host` 优先级最高） | `http://127.0.0.1:7009` |
| `MES_APP_ID` | 客户端标识，**必须 11 位** | `v2NDIyMTkzD` |
| `MES_APP_SECRET` | 客户端密钥，**必须 10 位** | `YWRtaW4iLC` |
| `MES_SYSTEM_ID` | 子系统 ID | `MES` |
| `MES_CUSTOMER_ID` | 分库客户 ID；留空则登录后自动取用户首个授权客户 | 空 |
| `MES_ORGANIZE_ID` | 工场 ID；留空则从登录响应带出 | 空 |
| `MES_USERNAME` / `MES_PASSWORD` | 登录账号（接口 2/3/4 必需） | 空 |
| `MES_TOKEN` | 预置 token，填了则跳过登录 | 空 |
| `MES_SKIP_REQUEST_LOG` | 加 `ClientID: jmeter`，跳过服务端请求日志落库 | `true` |
| `MES_PASSSTATION_IS_QUERY` | 接口 1 只校验不落库（**压测安全开关**） | `true` |
| `MES_STRICT` | `true` 时业务码非 200 一律判失败 | `false` |
| `MES_W_PS_INDEX` 等 4 个 | 各接口权重 | 3 / 3 / 2 / 2 |
| `MES_WAIT_MIN` / `MES_WAIT_MAX` | 用户思考时间区间（秒） | 1 / 3 |
| `MES_PS_SN`、`MES_SMT_LOT_NO`、`MES_WMS_FALIAO_NO` 等 | 业务数据，务必换成真实值 | DEMO 示例值 |

## 四、默认业务动作（避免污染数据）

压测默认走**只读/校验**路径，不会产生业务数据：

- 接口 1：`IsQuery=true`（`SNStatusInput.IsQuery`，为 true 时不保存数据）
- 接口 2：`Action=QueryInsideLotListInfo`（内部工单查询）
- 接口 3：`Check`（备料单核对，只读校验）
- 接口 4：`Action=Query`（仓位物料查询）

如需测写入路径，请显式改环境变量（如 `MES_WMS_BIN_ACTION=Add`、`MES_PASSSTATION_IS_QUERY=false`），**并确认压测环境数据可被写入**。

## 五、指标解读

- `code=200`：业务成功
- `code=400`：业务校验未通过（多为示例数据无效，非性能问题）。默认按“成功”统计，需要严格统计时设 `MES_STRICT=true`
- `code=410`：token 过期 → 检查 `MES_USERNAME/PASSWORD` 或 `MES_CUSTOMER_ID`
- `code=500` / 非 200：服务端异常，计为失败
- HTTP 层报“无法识别客户端”：多半是 AppID/AppSecret 位数不对

## 六、k8s 部署（对齐现有 `E:/MES3.0/k8s-deploy/mes-locust-load-generator.yaml`）

项目已有一个 locust 压测 Deployment（镜像 `hub.dbg.com.cn/mes/locust-load-generator`，
端口 8089，`LOCUST_HOST` 指向内网）。把本目录 `locustfile.py` 打进该镜像，或在 Deployment 的环境变量里
增加 `MES_APP_ID / MES_APP_SECRET / MES_USERNAME / MES_PASSWORD / MES_CUSTOMER_ID` 等即可复用。
`Dockerfile` 已提供本地构建方式。

## 七、注意事项

1. **不要对生产环境跑写入路径**；接口 1 的 `IsQuery`、接口 4 的 `Action=Query` 是默认安全档。
2. 后端按 `CustomerID` 分库，务必备好目标客户的 `MES_CUSTOMER_ID`，否则接口 2/3/4 会在分库校验处被拦截。
3. 每个虚拟用户 `on_start` 登录一次；高并发下登录接口自身也会产生压力，如需分离可先取好 token 再用 `MES_TOKEN`。
4. 脚本通过 Yarp 网关（`E:/MES3.0/YarpGateway`）访问各微服务；若直连单个服务，改 `--host` 即可。
