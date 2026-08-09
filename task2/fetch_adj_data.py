"""
获取恒瑞医药、平安银行的不复权 + 前复权数据，保存为 Excel（每只股票一个 xlsx，两个 sheet），
并生成扩展结构的 data.js（每只股票下 nofq/qfq 两套数据）。

策略：
  - 不复权行情：tushare pro_api.daily(...)
  - 复权因子：tushare pro_api.adj_factor(ts_code=a,b 批量)  只调用 1 次，绕过 adj_factor 1次/分钟 的限制
  - 本地计算前复权价格：qfq_price = nofq_price * (factor / latest_factor)
  - 成交量、成交额沿用不复权（两者一致，前复权只调整价）

用法:
    python fetch_adj_data.py --token YOUR_TUSHARE_TOKEN
或设置环境变量 TUSHARE_TOKEN 后直接:
    python fetch_adj_data.py
"""
import os
import sys
import argparse
import json
import warnings

warnings.filterwarnings("ignore")

import pandas as pd
import tushare as ts

STOCKS = [
    {"name": "恒瑞医药", "symbol": "600276", "exchange": "SH"},
    {"name": "平安银行", "symbol": "000001", "exchange": "SZ"},
]
START_DATE = "20250601"
END_DATE = "20260703"

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)  # ba_agent
OUT_DIR = HERE  # task2/ 下保存 Excel


def fetch_nofq(ts_code):
    df = ts.pro_api().daily(ts_code=ts_code, start_date=START_DATE, end_date=END_DATE)
    if df.empty:
        raise Exception(f"获取 {ts_code} 不复权数据为空")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["daily_return"] = df["close"].pct_change()
    return df.reset_index(drop=True)


def fetch_factors(ts_codes):
    """一次性获取所有股票的复权因子，避免 adj_factor 1次/分钟限制。"""
    codes_str = ",".join(ts_codes)
    df = ts.pro_api().adj_factor(ts_code=codes_str, start_date=START_DATE, end_date=END_DATE)
    if df.empty:
        raise Exception(f"adj_factor 接口返回空 (codes={codes_str})")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def apply_qfq(nofq_df, factors_for_stock):
    """基于最新日期的 adj_factor 计算前复权行情。"""
    # 合并复权因子到 nofq（按 trade_date，部分日期可能因停牌无因子，用前向填充）
    merged = nofq_df.merge(
        factors_for_stock[["trade_date", "adj_factor"]],
        on="trade_date", how="left",
    )
    merged["adj_factor"] = merged["adj_factor"].ffill().bfill()
    if pd.isna(merged["adj_factor"].iloc[-1]):
        raise Exception("复权因子缺失，无法计算前复权")
    latest_factor = merged["adj_factor"].iloc[-1]
    ratio = merged["adj_factor"] / latest_factor
    qfq = nofq_df.copy()
    for col in ["open", "high", "low", "close", "pre_close"]:
        if col in qfq.columns:
            # 前复权价按比率缩放；change = close - pre_close 也一起重算
            qfq[col] = (nofq_df[col] * ratio).round(4)
    # 重算 change / pct_chg / daily_return
    qfq["change"] = (qfq["close"] - qfq["pre_close"]).round(4)
    qfq["daily_return"] = qfq["close"].pct_change()
    qfq["pct_chg"] = (qfq["daily_return"] * 100).round(4)
    # vol / amount 保持不变（复权不影响量）
    return qfq.reset_index(drop=True)


def standard_cols(df):
    """列顺序与原 CSV 一致：ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount,daily_return"""
    cols = [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount", "daily_return",
    ]
    return df[cols].reset_index(drop=True)


def save_excel(df_nofq, df_qfq, stock_name):
    nofq = df_nofq.copy()
    nofq["trade_date"] = nofq["trade_date"].dt.strftime("%Y-%m-%d")
    qfq = df_qfq.copy()
    qfq["trade_date"] = qfq["trade_date"].dt.strftime("%Y-%m-%d")
    xlsx_path = os.path.join(OUT_DIR, f"{stock_name}.xlsx")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        nofq.to_excel(writer, sheet_name="不复权", index=False)
        qfq.to_excel(writer, sheet_name="前复权", index=False)
    print(f"  已保存 Excel: {xlsx_path}")
    return xlsx_path


def to_js_block(df):
    dates = df["trade_date"].dt.strftime("%Y-%m-%d").tolist()
    ohlc = [
        [round(r["open"], 4), round(r["close"], 4), round(r["low"], 4), round(r["high"], 4)]
        for _, r in df.iterrows()
    ]
    close = [round(x, 4) for x in df["close"].tolist()]
    vol = [round(x, 2) for x in df["vol"].tolist()]
    pct = [round(x * 100, 4) for x in df["daily_return"].fillna(0).tolist()]
    return {"dates": dates, "ohlc": ohlc, "close": close, "vol": vol, "pct": pct}


def build_datajs(all_data):
    data_obj = {}
    for stock in STOCKS:
        name = stock["name"]
        code = f'{stock["symbol"]}.{stock["exchange"]}'
        data_obj[name] = {
            "code": code,
            "nofq": to_js_block(all_data[name]["nofq"]),
            "qfq":  to_js_block(all_data[name]["qfq"]),
        }
    js_path = os.path.join(PROJECT_ROOT, "data.js")
    payload = json.dumps(data_obj, ensure_ascii=False, separators=(",", ":"))
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("const DATA = ")
        f.write(payload)
        f.write(";\n")
    total_bytes = os.path.getsize(js_path)
    print(f"  已生成 data.js: {js_path}  ({total_bytes} bytes)")
    for stock in STOCKS:
        name = stock["name"]
        nq = data_obj[name]["nofq"]
        qf = data_obj[name]["qfq"]
        print(f"  {name}: nofq {len(nq['dates'])} 天 / qfq {len(qf['dates'])} 天 | "
              f"首收盘 nofq={nq['close'][0]} qfq={qf['close'][0]} | "
              f"末收盘 nofq={nq['close'][-1]} qfq={qf['close'][-1]}")
    return js_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=None, help="Tushare Pro Token，不传则读取 TUSHARE_TOKEN 环境变量")
    args = ap.parse_args()

    token = args.token or os.environ.get("TUSHARE_TOKEN", "")
    if not token or token == "YOUR_TOKEN_HERE":
        print("错误: 未提供 TUSHARE_TOKEN，请用 --token 传入或设置环境变量 TUSHARE_TOKEN")
        sys.exit(1)

    ts.set_token(token)
    pro = ts.pro_api()

    ts_codes = [f"{s['symbol']}.{s['exchange']}" for s in STOCKS]

    print("1) 一次性获取两只股票的 adj_factor（规避频率限制）...")
    factors_df = fetch_factors(ts_codes)
    print(f"   获取 {len(factors_df)} 条因子记录")

    all_data = {}
    for idx, stock in enumerate(STOCKS):
        name = stock["name"]
        ts_code = ts_codes[idx]
        print(f"\n2.{idx+1}) [{name} ({ts_code})] 获取不复权 daily...")
        nofq = fetch_nofq(ts_code)
        print(f"    {len(nofq)} 条 | 日期 {nofq['trade_date'].min().date()} ~ {nofq['trade_date'].max().date()}")

        factors_for = factors_df[factors_df["ts_code"] == ts_code].sort_values("trade_date").reset_index(drop=True)
        print(f"    配对复权因子 {len(factors_for)} 条")
        qfq = apply_qfq(nofq, factors_for)
        print(f"    前复权计算完成，最后一天 nofq_close={round(nofq['close'].iloc[-1],4)} qfq_close={round(qfq['close'].iloc[-1],4)}")

        nofq_std = standard_cols(nofq)
        qfq_std = standard_cols(qfq)
        all_data[name] = {"nofq": nofq_std, "qfq": qfq_std}

        print(f"    保存 Excel 双 sheet...")
        save_excel(nofq_std, qfq_std, name)

    print("\n3) 生成扩展 data.js (nofq/qfq 嵌套结构)...")
    build_datajs(all_data)

    print("\n全部完成。")


if __name__ == "__main__":
    main()
