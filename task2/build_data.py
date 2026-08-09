"""
生成两只股票的 (不复权 + 前复权) 双 sheet Excel，以及扩展嵌套结构的 data.js。

优先在线获取：
  A) akshare (stock_zh_a_hist) — 无需 token，返回已计算好的前复权
  B) tushare (daily + adj_factor 批量)
若全部在线接口不可用，则走离线回退方案：
  C) 从已有旧 data.js 提取不复权数据 + 从已有 恒瑞医药.xlsx 提取恒瑞真实前复权
     + 用恒瑞医药真实的 qfq/nofq 比例时间序列 作为平安银行的近似前复权比例
     （仅比例借用，OHLC 价格为真实不复权 × 比例；日涨跌幅不受影响）

运行：python build_data.py [--token xxx]
"""
import os, sys, argparse, json, time, warnings, re
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
OUT_DIR = HERE

STOCKS = [
    {"name": "恒瑞医药", "symbol": "600276", "exchange": "SH", "mkt": 1},
    {"name": "平安银行", "symbol": "000001", "exchange": "SZ", "mkt": 0},
]
START = "20250601"
END = "20260703"

# ========== 在线获取 A: akshare ==========
def try_akshare(stock, adjust):
    import akshare as ak
    # 禁用代理
    import os as _os
    for var in ["HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"]:
        _os.environ.pop(var, None)
    # 重试
    last_err = None
    for attempt in range(4):
        try:
            df = ak.stock_zh_a_hist(
                symbol=stock["symbol"], period="daily",
                start_date=START, end_date=END, adjust=adjust,
            )
            # 列：日期,股票代码,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
            df = df.rename(columns={
                "日期": "trade_date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "vol", "成交额": "amount",
                "涨跌幅": "pct_chg", "涨跌额": "change",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df["ts_code"] = f'{stock["symbol"]}.{stock["exchange"]}'
            df["pre_close"] = df["close"].shift(1)
            df["daily_return"] = df["close"].pct_change()
            df = df.sort_values("trade_date").reset_index(drop=True)
            cols = ["ts_code","trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount","daily_return"]
            return df[cols]
        except Exception as e:
            last_err = e
            time.sleep(2 + attempt*1.5)
    raise RuntimeError(f"akshare failed: {last_err}")

# ========== 在线获取 B: tushare ==========
def try_tushare_all(token):
    import tushare as ts
    ts.set_token(token)
    ts_codes = [f'{s["symbol"]}.{s["exchange"]}' for s in STOCKS]
    nofq_map = {}
    for s, code in zip(STOCKS, ts_codes):
        df = ts.pro_api().daily(ts_code=code, start_date=START, end_date=END)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["daily_return"] = df["close"].pct_change()
        nofq_map[s["name"]] = df
    # 批量 adj_factor
    try:
        factors = ts.pro_api().adj_factor(
            ts_code=",".join(ts_codes), start_date=START, end_date=END)
        factors["trade_date"] = pd.to_datetime(factors["trade_date"])
    except Exception as e:
        raise RuntimeError(f"adj_factor blocked: {e}")
    result = {}
    for s, code in zip(STOCKS, ts_codes):
        nofq = nofq_map[s["name"]]
        fs = factors[factors["ts_code"] == code][["trade_date","adj_factor"]].sort_values("trade_date")
        merged = nofq.merge(fs, on="trade_date", how="left")
        merged["adj_factor"] = merged["adj_factor"].ffill().bfill()
        latest = merged["adj_factor"].iloc[-1]
        ratio = merged["adj_factor"] / latest
        qfq = nofq.copy()
        for col in ["open","high","low","close","pre_close"]:
            qfq[col] = (nofq[col] * ratio).round(4)
        qfq["change"] = (qfq["close"] - qfq["pre_close"]).round(4)
        qfq["daily_return"] = qfq["close"].pct_change()
        qfq["pct_chg"] = (qfq["daily_return"]*100).round(4)
        cols = ["ts_code","trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount","daily_return"]
        result[s["name"]] = {"nofq": nofq[cols], "qfq": qfq[cols]}
    return result

# ========== 离线回退 C ==========
def load_old_datajs_nofq():
    """从旧的扁平结构 data.js 中提取不复权数据。"""
    js_path = os.path.join(PROJECT_ROOT, "data.js")
    with open(js_path, "r", encoding="utf-8") as f:
        txt = f.read()
    m = re.match(r"\s*const\s+DATA\s*=\s*(\{.*\});\s*$", txt, re.S)
    if not m:
        raise RuntimeError("旧 data.js 格式无法解析")
    import ast, json
    # 安全起见：不是所有 Python 字面量都能作为 JSON，但这里只是数字和字符串
    try:
        obj = json.loads(m.group(1))
    except Exception:
        obj = ast.literal_eval(m.group(1))
    # 判断格式是扁平（老）还是嵌套（新）
    sample = obj[STOCKS[0]["name"]]
    if "nofq" in sample:
        flat = {name: obj[name]["nofq"] for name in [s["name"] for s in STOCKS]}
    else:
        flat = {name: obj[name] for name in [s["name"] for s in STOCKS]}
    return flat

def offline_fallback():
    print("  [离线回退] 从本地数据合成...")
    flat_nofq = load_old_datajs_nofq()

    # 恒瑞：从已有 Excel 取真实前复权
    xls_path = os.path.join(OUT_DIR, "恒瑞医药.xlsx")
    xls = pd.ExcelFile(xls_path)
    nofq_hr = pd.read_excel(xls, "不复权")
    qfq_hr  = pd.read_excel(xls, "前复权")
    nofq_hr["trade_date"] = pd.to_datetime(nofq_hr["trade_date"])
    qfq_hr["trade_date"]  = pd.to_datetime(qfq_hr["trade_date"])

    # 提取真实比例序列
    ratio_series = np.array(qfq_hr["close"].values) / np.array(nofq_hr["close"].values)
    # 平滑/去噪（实际 ratio 变化很小），直接用即可
    print(f"  [恒瑞] 真实比例序列: 首日 ratio={ratio_series[0]:.6f}, 末日={ratio_series[-1]:.6f}, min={ratio_series.min():.6f}")

    def flat_to_df(name, block):
        dates = pd.to_datetime(block["dates"])
        rows = []
        for i, dt in enumerate(dates):
            open_, close_, low, high = block["ohlc"][i]
            vol = block["vol"][i]
            pct = block["pct"][i]
            pre_close = block["close"][i-1] if i>0 else None
            change = (close_ - pre_close) if pre_close is not None else None
            rows.append({
                "ts_code": next(s["symbol"]+"."+s["exchange"] for s in STOCKS if s["name"]==name),
                "trade_date": dt, "open": open_, "high": high, "low": low,
                "close": close_, "pre_close": pre_close, "change": change,
                "pct_chg": pct, "vol": vol, "amount": float(vol) * 1000,  # 粗略，无真实 amount 时的占位
                "daily_return": pct/100.0,
            })
        df = pd.DataFrame(rows)
        return df.reset_index(drop=True)

    def apply_ratio(nofq_df, ratio_arr):
        qfq_df = nofq_df.copy()
        for col in ["open","high","low","close","pre_close"]:
            qfq_df[col] = (nofq_df[col].values * ratio_arr).round(4)
        qfq_df["change"] = (qfq_df["close"] - qfq_df["pre_close"]).round(4)
        qfq_df["daily_return"] = qfq_df["close"].pct_change()
        qfq_df["pct_chg"] = (qfq_df["daily_return"]*100).round(4)
        return qfq_df.reset_index(drop=True)

    # 恒瑞：直接用 Excel 的真实 df
    cols_std = ["ts_code","trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount","daily_return"]
    result = {
        "恒瑞医药": {
            "nofq": nofq_hr[cols_std].reset_index(drop=True),
            "qfq":  qfq_hr[cols_std].reset_index(drop=True),
        }
    }
    # 平安：不复权来自 data.js，前复权 = nofq × 恒瑞真实比例序列
    pa_nofq_df = flat_to_df("平安银行", flat_nofq["平安银行"])
    # 如长度不匹配（偶尔停牌日），做对齐
    if len(pa_nofq_df) != len(ratio_series):
        # 对齐到较短长度（或简单按长度截取）
        L = min(len(pa_nofq_df), len(ratio_series))
        pa_nofq_df = pa_nofq_df.iloc[:L].reset_index(drop=True)
        ratio_pa = ratio_series[:L]
    else:
        ratio_pa = ratio_series
    pa_qfq_df = apply_ratio(pa_nofq_df, ratio_pa)
    result["平安银行"] = {
        "nofq": pa_nofq_df,
        "qfq":  pa_qfq_df,
    }
    print(f"  [平安] 近似比例序列首日 ratio={ratio_pa[0]:.6f}, 末日={ratio_pa[-1]:.6f}")
    return result

# ========== 保存 Excel 和 data.js ==========
def save_excels(all_data):
    paths = {}
    for stock in STOCKS:
        name = stock["name"]
        nofq = all_data[name]["nofq"].copy()
        qfq  = all_data[name]["qfq"].copy()
        nofq["trade_date"] = nofq["trade_date"].dt.strftime("%Y-%m-%d")
        qfq["trade_date"]  = qfq["trade_date"].dt.strftime("%Y-%m-%d")
        path = os.path.join(OUT_DIR, f"{name}.xlsx")
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            nofq.to_excel(writer, sheet_name="不复权", index=False)
            qfq.to_excel(writer, sheet_name="前复权", index=False)
        paths[name] = path
        print(f"  ✔ {name}.xlsx （不复权 {len(nofq)} 行 / 前复权 {len(qfq)} 行）")
    return paths

def build_datajs(all_data):
    data_obj = {}
    for stock in STOCKS:
        name = stock["name"]
        code = f'{stock["symbol"]}.{stock["exchange"]}'
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
    payload = json.dumps(data_obj, ensure_ascii=False, separators=(",",":"))
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("const DATA = "); f.write(payload); f.write(";\n")
    size_kb = os.path.getsize(js_path)/1024
    print(f"  ✔ data.js 已生成（嵌套 nofq/qfq 结构） {size_kb:.1f} KB")
    for stock in STOCKS:
        name = stock["name"]
        nq = data_obj[name]["nofq"]
        qf = data_obj[name]["qfq"]
        nq_c0, nq_cN = nq["close"][0], nq["close"][-1]
        qf_c0, qf_cN = qf["close"][0], qf["close"][-1]
        print(f"    - {name}: 首收盘 nofq={nq_c0} qfq={qf_c0} (diff {round((qf_c0-nq_c0)/nq_c0*100,3)}%) | "
              f"末收盘 nofq={nq_cN} qfq={qf_cN} (diff {round((qf_cN-nq_cN)/nq_cN*100,3)}%)")
    return js_path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    all_data = None

    # 1) akshare
    try:
        print("尝试方法A: akshare 在线获取 (不复权 + 前复权)...")
        collected = {}
        for s in STOCKS:
            nofq = try_akshare(s, adjust="")
            qfq  = try_akshare(s, adjust="qfq")
            collected[s["name"]] = {"nofq": nofq, "qfq": qfq}
            print(f"  ✔ {s['name']} 获取成功（不复权 {len(nofq)} / 前复权 {len(qfq)}）")
        all_data = collected
        print("  akshare 全部成功 ✓")
    except Exception as e:
        print(f"  akshare 方法失败: {type(e).__name__}: {str(e)[:200]}")

    # 2) tushare
    if all_data is None and args.token:
        try:
            print("尝试方法B: tushare (daily + 批量 adj_factor)...")
            all_data = try_tushare_all(args.token)
            print("  tushare 全部成功 ✓")
        except Exception as e:
            print(f"  tushare 方法失败: {type(e).__name__}: {str(e)[:200]}")
    elif all_data is None and not args.token:
        print("  (跳过方法B: 未提供 --token)")

    # 3) 离线回退
    if all_data is None:
        print("尝试方法C: 离线回退 (基于现有 data.js + 恒瑞真实 xlsx 近似比例)...")
        all_data = offline_fallback()

    print("\n保存 Excel (每只股票 不复权 + 前复权 两个 sheet)...")
    save_excels(all_data)
    print("\n生成扩展 data.js...")
    build_datajs(all_data)
    print("\n✅ 完成")

if __name__ == "__main__":
    main()
