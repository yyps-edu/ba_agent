"""
fetch_data.py — 股票数据获取 + 生成 data.js

功能：
  1) 通过 AKShare 获取股票不复权 + 前复权数据（东财→腾讯 双源容灾）
  2) 生成根目录 data.js（嵌套 nofq/qfq 结构，供所有看板使用）
  * 不再生成 Excel（如需指标计算/绘图，看板已用 data.js 运行时计算）

命令行：
  python fetch_data.py              # 拉取所有股票数据 → 生成 data.js
"""
import os, sys, time, socket, warnings, json
warnings.filterwarnings("ignore")

# ========== 全局路径与配置 ==========
HERE = os.path.dirname(os.path.abspath(__file__))

STOCKS = [
    {"name": "恒瑞医药", "symbol": "600276"},
    {"name": "平安银行", "symbol": "000001"},
]
START = "20240801"
END   = "20260810"

# ========== 第一层：彻底禁用代理 ==========
PROXY_VARS = [
    "HTTP_PROXY","HTTPS_PROXY","ALL_PROXY",
    "http_proxy","https_proxy","all_proxy",
    "REQUESTS_CA_BUNDLE","CURL_CA_BUNDLE",
]
for var in PROXY_VARS:
    os.environ.pop(var, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import urllib.request
urllib.request.getproxies = lambda: {}

try:
    import requests
    _orig_si = requests.Session.__init__
    def _patched_si(self, *args, **kwargs):
        _orig_si(self, *args, **kwargs)
        self.trust_env = False
        self.proxies.clear()
        self.proxies.update({"http": None, "https": None})
    requests.Session.__init__ = _patched_si
    _orig_req = requests.api.request
    def _patched_req(method, url, **kwargs):
        kwargs.setdefault("proxies", {"http": None, "https": None})
        if "timeout" not in kwargs:
            kwargs["timeout"] = 30
        return _orig_req(method, url, **kwargs)
    requests.api.request = _patched_req
except Exception:
    pass

socket.setdefaulttimeout(30)

import pandas as pd
import numpy as np

# ========== 网络工具 ==========
def _tcp_test(host, port=443, timeout=10):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False

# ========== 1. 数据获取：AKShare（东财 → 腾讯 双源） ==========
def _normalize_ak_df(df, start=START, end=END):
    tx_cols = {"date", "open", "close", "high", "low", "amount"}
    if tx_cols.issubset(set(df.columns)) and "日期" not in df.columns:
        if "vol" not in df.columns:
            df = df.copy()
            df["__tmp_mid"] = (df["open"].fillna(0) + df["close"].fillna(0)) / 2
            df["__tmp_mid"] = df["__tmp_mid"].replace(0, np.nan).ffill().bfill().fillna(1)
            df["vol"] = (df["amount"].fillna(0) / df["__tmp_mid"]).round(0).astype(np.int64)
            df = df.drop(columns=["__tmp_mid"])
        df = df.rename(columns={
            "date": "日期", "open": "开盘", "close": "收盘",
            "high": "最高", "low": "最低", "vol": "成交量", "amount": "成交额",
        })

    col_map = {
        "日期": "trade_date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "vol",
        "成交额": "amount", "涨跌幅": "pct_chg", "涨跌额": "change",
    }
    df = df.rename(columns=col_map)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    start_dt = pd.to_datetime(start, format="%Y%m%d")
    end_dt   = pd.to_datetime(end,   format="%Y%m%d")
    df = df[(df["trade_date"] >= start_dt) & (df["trade_date"] <= end_dt)]
    df = df.sort_values("trade_date").reset_index(drop=True)

    if "pre_close" not in df.columns:
        df["pre_close"] = df["close"].shift(1)
    if "change" not in df.columns:
        df["change"] = df["close"] - df["pre_close"]
    if "pct_chg" not in df.columns:
        df["pct_chg"] = np.where(df["pre_close"].fillna(0) != 0,
                                 df["change"] / df["pre_close"] * 100, np.nan)
    if "daily_return" not in df.columns:
        df["daily_return"] = df["close"].pct_change()

    df["vol"]    = pd.to_numeric(df["vol"], errors="coerce").fillna(0).astype(np.int64)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)

    cols = ["trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount","daily_return"]
    return df[cols]

def _tx_symbol(symbol):
    if symbol.startswith(("6", "9")):
        return "sh" + symbol
    return "sz" + symbol

def fetch_stock(symbol, adjust):
    import akshare as ak
    last_err = None
    em_max_attempts = 1
    tx_max_attempts = 3

    for attempt in range(1, em_max_attempts + 1):
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol, period="daily",
                start_date=START, end_date=END, adjust=adjust,
            )
            if df is None or df.empty:
                raise RuntimeError(f"返回空 shape={getattr(df,'shape',None)}")
            return _normalize_ak_df(df)
        except Exception as e:
            last_err = e
            wait = 0
            tag = f"[东财 {symbol} ad={adjust!r}]"
            print(f"    {tag} {attempt}/{em_max_attempts} 失败: {type(e).__name__}: {str(e)[:120]} → {wait}s")
            if wait:
                time.sleep(wait)

    print(f"    → 东方财富失败，切换腾讯 stock_zh_a_hist_tx 接口…")

    tx_sym = _tx_symbol(symbol)
    tx_adjust = None if adjust == "" else adjust
    for attempt in range(1, tx_max_attempts + 1):
        try:
            df = ak.stock_zh_a_hist_tx(
                symbol=tx_sym, start_date=START, end_date=END, adjust=tx_adjust,
            )
            if df is None or df.empty:
                raise RuntimeError(f"腾讯返回空 shape={getattr(df,'shape',None)}")
            return _normalize_ak_df(df)
        except Exception as e:
            last_err = e
            wait = min(2 ** min(attempt, 3), 8) if attempt < tx_max_attempts else 0
            print(f"    [腾讯 {tx_sym} ad={adjust!r}] {attempt}/{tx_max_attempts} 失败: {type(e).__name__}: {str(e)[:120]} → {wait}s")
            if wait:
                time.sleep(wait)
    raise RuntimeError(f"ak {symbol} adjust={adjust!r} 全部源失败, 最后错误: {last_err}")

def fetch_all():
    print("\n[网络预检测] eastmoney / baidu / qq...")
    for host in ["push2his.eastmoney.com","proxy.finance.qq.com","www.baidu.com"]:
        print(f"  {host}: {'✅' if _tcp_test(host) else '❌'}")

    result = {}
    for st in STOCKS:
        name, sym = st["name"], st["symbol"]
        code = sym + (".SH" if sym.startswith(("6","9")) else ".SZ")
        print(f"\n>>> 拉取 {name} ({sym})...")
        nofq = fetch_stock(sym, adjust="")
        print(f"    [不复权] {len(nofq)} 行  {nofq.trade_date.iloc[0].date()} ~ {nofq.trade_date.iloc[-1].date()}")
        qfq  = fetch_stock(sym, adjust="qfq")
        print(f"    [前复权 ] {len(qfq)} 行  {qfq.trade_date.iloc[0].date()} ~ {qfq.trade_date.iloc[-1].date()}")
        d0 = (qfq["close"].iloc[0] - nofq["close"].iloc[0]) / nofq["close"].iloc[0] * 100
        dN = (qfq["close"].iloc[-1] - nofq["close"].iloc[-1]) / nofq["close"].iloc[-1] * 100
        print(f"    → 首收盘 diff {d0:+.3f}%  |  末收盘 diff {dN:+.3f}% (前复权末日应为 0%)")
        result[name] = {"code": code, "nofq": nofq, "qfq": qfq}
    return result

# ========== 2. 生成 data.js ==========
def build_datajs(all_data):
    data_obj = {}
    for st in STOCKS:
        name = st["name"]
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
    js_path = os.path.join(HERE, "data.js")
    payload = json.dumps(data_obj, ensure_ascii=False, separators=(",",":"))
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("const DATA = "); f.write(payload); f.write(";\n")
    print(f"  ✔ data.js  {os.path.getsize(js_path)/1024:.1f} KB  嵌套 nofq/qfq 结构")

# ========== 主函数 ==========
def main():
    print("=" * 64)
    print("fetch_data.py  (AKShare 双源不复权/前复权 → data.js)")
    print(f"  日期范围: {START} ~ {END}")
    print(f"  股票池: {STOCKS}")
    print("=" * 64)

    all_data = fetch_all()

    print("\n▶ 生成根目录 data.js（供 HTML 看板使用）...")
    build_datajs(all_data)

    print("\n✅ 全部完成！")

if __name__ == '__main__':
    main()
