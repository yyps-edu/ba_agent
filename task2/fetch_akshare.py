"""
专用 AKShare 脚本：获取恒瑞医药 + 平安银行的 不复权 + 前复权 历史数据

特点：
  1. 调用前彻底禁用代理（清除所有代理环境变量 + monkey-patch urllib.request + requests 禁代理）
  2. 每只股票/每种 adjust 最多重试 8 次，指数退避 2s → 20s
  3. 显式传入 symbol=纯代码、start_date/end_date=YYYYMMDD、adjust='qfq'/''、period='daily'
  4. 完成后覆盖生成 xlsx（双 sheet）和 data.js（嵌套 nofq/qfq）

运行：
  D:\\ProgramData\\anaconda3\\python.exe task2/fetch_akshare.py
"""
import os, sys, time, socket, warnings
warnings.filterwarnings("ignore")

# ==================== 第一步：彻底禁用代理 ====================
# 1) 清环境变量
PROXY_VARS = [
    "HTTP_PROXY","HTTPS_PROXY","ALL_PROXY",
    "http_proxy","https_proxy","all_proxy",
    "REQUESTS_CA_BUNDLE","CURL_CA_BUNDLE",
]
for var in PROXY_VARS:
    os.environ.pop(var, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

# 2) monkey-patch urllib.request.getproxies → 永远返回空 dict
import urllib.request
urllib.request.getproxies = lambda: {}

# 3) 如果有 requests 库，也全局设置 trust_env=False 并清代理
try:
    import requests
    _orig_session_init = requests.Session.__init__
    def _patched_session_init(self, *args, **kwargs):
        _orig_session_init(self, *args, **kwargs)
        self.trust_env = False
        self.proxies.clear()
        self.proxies.update({"http": None, "https": None})
    requests.Session.__init__ = _patched_session_init
    # 同时 monkey-patch requests.api 的 request 函数
    _orig_request = requests.api.request
    def _patched_request(method, url, **kwargs):
        kwargs.setdefault("proxies", {"http": None, "https": None})
        if "timeout" not in kwargs:
            kwargs["timeout"] = 30
        return _orig_request(method, url, **kwargs)
    requests.api.request = _patched_request
except Exception:
    pass

# 4) socket 级别的默认超时
socket.setdefaulttimeout(30)

# ==================== 连接性预检测 ====================
def _tcp_connect_test(host, port=443, timeout=10):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception as e:
        print(f"  [TCP预检测] {host}:{port} 失败: {e}")
        return False

import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
OUT_DIR = HERE

STOCKS = [
    {"name": "恒瑞医药", "symbol": "600276"},
    {"name": "平安银行", "symbol": "000001"},
]
START = "20250601"
END   = "20260703"

# ==================== 核心：AKShare 调用 ====================
def fetch_ak(symbol, adjust):
    """
    adjust: '' → 不复权, 'qfq' → 前复权
    返回 df 标准化列：trade_date, open, high, low, close, vol, amount, pct_chg, change, pre_close, daily_return
    """
    import akshare as ak
    last_err = None
    for attempt in range(1, 9):
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=START,
                end_date=END,
                adjust=adjust,
            )
            if df is None or df.empty:
                raise RuntimeError(f"ak.stock_zh_a_hist 返回空数据 shape={getattr(df,'shape',None)}")
            # 标准列名：日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
            col_map = {
                "日期": "trade_date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "vol",
                "成交额": "amount", "涨跌幅": "pct_chg", "涨跌额": "change",
            }
            df = df.rename(columns=col_map)
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df["pre_close"] = df["close"].shift(1)
            df["daily_return"] = df["close"].pct_change()
            df = df.sort_values("trade_date").reset_index(drop=True)
            cols = ["trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount","daily_return"]
            return df[cols]
        except Exception as e:
            last_err = e
            wait = 2 ** min(attempt, 4)  # 2, 4, 8, 16, 20, 20, 20, 20
            wait = min(wait, 20)
            print(f"  [ak {symbol} adjust={adjust!r}] attempt {attempt}/8 失败: {type(e).__name__}: {str(e)[:180]} → 等待 {wait}s")
            time.sleep(wait)
            # 第5次尝试之后，再做一次 socket 预检测，确认网络可达
            if attempt == 4:
                host = "push2his.eastmoney.com"
                if not _tcp_connect_test(host):
                    print(f"    → TCP 无法连接 {host}:443，等待 15s 再试一次 DNS/TCP...")
                    time.sleep(15)
                    _tcp_connect_test(host)
    raise RuntimeError(f"ak {symbol} adjust={adjust!r} 全部 {8} 次尝试失败, 最后错误: {last_err}")

# ==================== 保存 Excel 和 data.js ====================
def save_excels(all_data):
    for stock in STOCKS:
        name = stock["name"]
        nofq = all_data[name]["nofq"].copy()
        qfq  = all_data[name]["qfq"].copy()
        nofq["ts_code"] = all_data[name]["code"]
        qfq["ts_code"]  = all_data[name]["code"]
        nofq["trade_date"] = nofq["trade_date"].dt.strftime("%Y-%m-%d")
        qfq["trade_date"]  = qfq["trade_date"].dt.strftime("%Y-%m-%d")
        cols_std = ["ts_code","trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount","daily_return"]
        path = os.path.join(OUT_DIR, f"{name}.xlsx")
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            nofq[cols_std].to_excel(writer, sheet_name="不复权", index=False)
            qfq[cols_std].to_excel(writer, sheet_name="前复权", index=False)
        print(f"  ✔ {name}.xlsx  (不复权 {len(nofq)} / 前复权 {len(qfq)} 行)")

def build_datajs(all_data):
    data_obj = {}
    for stock in STOCKS:
        name = stock["name"]
        code = all_data[name]["code"]
        def to_block(df):
            dates = df["trade_date"].dt.strftime("%Y-%m-%d").tolist()
            ohlc = [[round(r["open"],4), round(r["close"],4), round(r["low"],4), round(r["high"],4)]
                    for _, r in df.iterrows()]
            close = [round(float(x),4) for x in df["close"].tolist()]
            vol   = [round(float(x),2) for x in df["vol"].tolist()]
            pct   = [round(float(x)*100,4) for x in df["daily_return"].fillna(0).tolist()]
            return {"dates":dates,"ohlc":ohlc,"close":close,"vol":vol,"pct":pct}
        data_obj[name] = {
            "code": code,
            "nofq": to_block(all_data[name]["nofq"]),
            "qfq":  to_block(all_data[name]["qfq"]),
        }
    js_path = os.path.join(PROJECT_ROOT, "data.js")
    import json
    payload = json.dumps(data_obj, ensure_ascii=False, separators=(",",":"))
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("const DATA = "); f.write(payload); f.write(";\n")
    print(f"  ✔ data.js 已生成 ({os.path.getsize(js_path)/1024:.1f} KB)")
    for stock in STOCKS:
        name = stock["name"]
        nq = data_obj[name]["nofq"]
        qf = data_obj[name]["qfq"]
        nq_c0, nq_cN = nq["close"][0], nq["close"][-1]
        qf_c0, qf_cN = qf["close"][0], qf["close"][-1]
        print(f"    - {name}: 首收盘 nofq={nq_c0} qfq={qf_c0} diff {round((qf_c0-nq_c0)/nq_c0*100,3)}% | "
              f"末收盘 nofq={nq_cN} qfq={qf_cN} diff {round((qf_cN-nq_cN)/nq_cN*100,3)}%")

def main():
    print("=" * 60)
    print("AKShare 专用数据获取脚本（不复权 + 前复权）")
    print(f"  日期范围: {START} ~ {END}")
    print(f"  股票: {[s['name'] + '(' + s['symbol'] + ')' for s in STOCKS]}")
    print("=" * 60)

    # 预检测 TCP 连接
    print("\n[网络预检测] TCP 连接 eastmoney...")
    ok1 = _tcp_connect_test("push2his.eastmoney.com", 443, timeout=10)
    ok2 = _tcp_connect_test("www.baidu.com", 443, timeout=10)
    print(f"  eastmoney: {'✅' if ok1 else '❌'}  baidu: {'✅' if ok2 else '❌'}")

    all_data = {}
    for stock in STOCKS:
        name, sym = stock["name"], stock["symbol"]
        code = sym + (".SH" if sym.startswith(("6","9")) else ".SZ")
        print(f"\n>>> 拉取 {name} ({sym})...")
        nofq = fetch_ak(sym, adjust="")
        print(f"    [不复权] {len(nofq)} 行, 日期 {nofq.trade_date.iloc[0].date()} ~ {nofq.trade_date.iloc[-1].date()}")
        qfq = fetch_ak(sym, adjust="qfq")
        print(f"    [前复权 ] {len(qfq)} 行, 日期 {qfq.trade_date.iloc[0].date()} ~ {qfq.trade_date.iloc[-1].date()}")
        all_data[name] = {"code": code, "nofq": nofq, "qfq": qfq}
        diff_ratio = (qfq["close"].iloc[0] - nofq["close"].iloc[0]) / nofq["close"].iloc[0] * 100
        print(f"    → 首日收盘价 nofq={nofq['close'].iloc[0]} vs qfq={qfq['close'].iloc[0]} (差异 {diff_ratio:.3f}%)")
        print(f"    → 末日收盘价 nofq={nofq['close'].iloc[-1]} vs qfq={qfq['close'].iloc[-1]}")

    print("\n写入 Excel...")
    save_excels(all_data)
    print("\n生成 data.js (嵌套 nofq/qfq 结构)...")
    build_datajs(all_data)
    print("\n✅ 全部完成！")

if __name__ == "__main__":
    main()
