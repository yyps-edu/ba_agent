"""
stock_analysis.py — 股票数据获取 + 指标计算 + 可视化（融合版）

功能模块：
  1) 数据获取：AKShare (优先东方财富 stock_zh_a_hist，失败自动切换腾讯 stock_zh_a_hist_tx)
     — 每只股票获取 不复权('') + 前复权('qfq') 两套数据
     — 内置：彻底禁用代理 + TCP 预检测 + 8 次指数退避重试
  2) 数据保存：
     — 每只股票 1 个 Excel（不复权/前复权 双 sheet）
     — 根目录 data.js（嵌套 nofq/qfq 结构，供 index.html 看板使用）
  3) 指标计算：RSI / MACD / 布林带 / KDJ（基于不复权）
  4) 可视化：matplotlib 绘图（收盘价/收益率分布/RSI/MACD/布林带/K线/KDJ）

命令行：
  默认：拉取双股票数据 → 保存 xlsx + data.js → 对恒瑞医药(不复权)算指标+绘图
  --fetch-only 只拉取并保存，不绘图
  --stock 恒瑞医药  指定单只绘图（默认恒瑞医药）
  python task2/stock_analysis.py
"""
import os, sys, time, socket, warnings, argparse, json
warnings.filterwarnings("ignore")

# ========== 全局路径与配置 ==========
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
OUT_DIR = HERE

STOCKS = [
    {"name": "恒瑞医药", "symbol": "600276"},
    {"name": "平安银行", "symbol": "000001"},
]
# 近 10 年：2016-08-01 ~ 2026-08-10（今天）
START = "20160801"
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
    """
    统一列名 + 计算 daily_return + 过滤日期。
    兼容两种源：
      - 东方财富（中文列）：日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅/涨跌额
      - 腾讯（英文列）：date/open/close/high/low/amount（缺vol/change/pct_chg，需补算）
    """
    # --- 1) 腾讯分支：英文列 → 中文列（保证后续 col_map 可统一命中） ---
    tx_cols = {"date", "open", "close", "high", "low", "amount"}
    if tx_cols.issubset(set(df.columns)) and "日期" not in df.columns:
        # 若腾讯返回 amount 单位不明，优先用 amount 估成交量（量柱只看相对大小，不做精确显示）
        if "vol" not in df.columns:
            df = df.copy()
            # 腾讯接口无成交量列，用成交额/一个近似价估 vol，或直接 = amount（归一化显示即可）
            df["__tmp_mid"] = (df["open"].fillna(0) + df["close"].fillna(0)) / 2
            df["__tmp_mid"] = df["__tmp_mid"].replace(0, np.nan).ffill().bfill().fillna(1)
            df["vol"] = (df["amount"].fillna(0) / df["__tmp_mid"]).round(0).astype(np.int64)
            df = df.drop(columns=["__tmp_mid"])
        df = df.rename(columns={
            "date": "日期", "open": "开盘", "close": "收盘",
            "high": "最高", "low": "最低", "vol": "成交量", "amount": "成交额",
        })

    # --- 2) 中文列统一映射 ---
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

    # --- 3) 缺失字段补算（腾讯源缺 change/pct_chg/pre_close/daily_return） ---
    if "pre_close" not in df.columns:
        df["pre_close"] = df["close"].shift(1)
    if "change" not in df.columns:
        df["change"] = df["close"] - df["pre_close"]
    if "pct_chg" not in df.columns:
        df["pct_chg"] = np.where(df["pre_close"].fillna(0) != 0,
                                 df["change"] / df["pre_close"] * 100, np.nan)
    if "daily_return" not in df.columns:
        df["daily_return"] = df["close"].pct_change()

    # 若 vol/amount 缺失（极端情况）填 0 保证不崩
    df["vol"]    = pd.to_numeric(df["vol"], errors="coerce").fillna(0).astype(np.int64)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)

    cols = ["trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount","daily_return"]
    return df[cols]

def _tx_symbol(symbol):
    """给 6/9 开头加 sh，0/3 开头加 sz（腾讯接口要求带市场前缀）"""
    if symbol.startswith(("6", "9")):
        return "sh" + symbol
    return "sz" + symbol

def fetch_stock(symbol, adjust):
    """
    获取单只股票不复权或前复权数据。
    adjust: ''（不复权）或 'qfq'（前复权）
    优先用东方财富 stock_zh_a_hist，失败快速切换腾讯 stock_zh_a_hist_tx。
    """
    import akshare as ak
    last_err = None
    em_max_attempts = 1   # 东财已被封 → 1 次快速跳过（0s等待）
    tx_max_attempts = 3   # 腾讯稳定 → 3 次足够

    # ========== 源1: 东方财富 stock_zh_a_hist ==========
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

    # ========== 源2: 腾讯 stock_zh_a_hist_tx ==========
    # 注意：腾讯接口①无 period 参数 ②symbol 必须带 sh/sz 前缀 ③adjust: None='' 或 'qfq'
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
    """拉取所有股票的不复权 + 前复权。"""
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

# ========== 2. 保存：Excel 双 sheet + data.js ==========
def save_excels(all_data):
    cols_std = ["ts_code","trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount","daily_return"]
    for st in STOCKS:
        name = st["name"]
        nofq = all_data[name]["nofq"].copy()
        qfq  = all_data[name]["qfq"].copy()
        nofq["ts_code"] = all_data[name]["code"]
        qfq["ts_code"]  = all_data[name]["code"]
        nofq["trade_date"] = nofq["trade_date"].dt.strftime("%Y-%m-%d")
        qfq["trade_date"]  = qfq["trade_date"].dt.strftime("%Y-%m-%d")
        path = os.path.join(OUT_DIR, f"{name}.xlsx")
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            nofq[cols_std].to_excel(w, sheet_name="不复权", index=False)
            qfq[cols_std].to_excel(w,  sheet_name="前复权", index=False)
        print(f"  ✔ {name}.xlsx  不复权 {len(nofq)} / 前复权 {len(qfq)} 行")

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
    js_path = os.path.join(PROJECT_ROOT, "data.js")
    payload = json.dumps(data_obj, ensure_ascii=False, separators=(",",":"))
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("const DATA = "); f.write(payload); f.write(";\n")
    print(f"  ✔ data.js  {os.path.getsize(js_path)/1024:.1f} KB  嵌套 nofq/qfq 结构")

# ========== 3. 指标计算（不复权 df） ==========
def calculate_indicators(df):
    delta = df['close'].diff(1)
    gain, loss = delta.where(delta > 0, 0), -delta.where(delta < 0, 0)
    df['rsi'] = 100 - (100 / (1 + gain.rolling(14).mean() / loss.rolling(14).mean()))

    df['ema_fast'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema_fast'] - df['ema_slow']
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['histogram'] = df['macd'] - df['signal']

    df['bb_middle'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_middle'] + 2 * df['bb_std']
    df['bb_lower'] = df['bb_middle'] - 2 * df['bb_std']

    low_list, high_list = df['low'].rolling(9).min(), df['high'].rolling(9).max()
    rsv = (df['close'] - low_list) / (high_list - low_list) * 100
    df['k'] = rsv.ewm(com=2, adjust=False).mean()
    df['d'] = df['k'].ewm(com=2, adjust=False).mean()
    df['j'] = 3 * df['k'] - 2 * df['d']
    return df

# ========== 4. matplotlib 绘图（输出到 task2 目录） ==========
def _outp(name):
    return os.path.join(OUT_DIR, name)

def plot_close_price(df, stock_name):
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(df['trade_date'], df['close'], color='blue', linewidth=2)
    ax.set_ylim(df['close'].min() * 0.98, df['close'].max() * 1.02)
    ax.set_title(f'{stock_name} 收盘价走势图', fontsize=14)
    ax.set_xlabel('日期'); ax.set_ylabel('收盘价 (元)')
    ax.grid(True, alpha=0.3); plt.xticks(rotation=45); plt.tight_layout()
    plt.savefig(_outp('close_price.png'), dpi=150, bbox_inches='tight'); plt.close()

def plot_return_distribution(df, stock_name):
    fig, ax = plt.subplots(figsize=(12, 6))
    returns = df['daily_return'].dropna()
    ax.hist(returns, bins=50, color='blue', alpha=0.7, edgecolor='black')
    ax.axvline(returns.mean(), color='red', linestyle='--', linewidth=2, label=f'均值: {returns.mean():.4f}')
    ax.axvline(returns.mean() + returns.std(), color='green', linestyle='--', label=f'+1σ: {returns.mean()+returns.std():.4f}')
    ax.axvline(returns.mean() - returns.std(), color='green', linestyle='--', label=f'-1σ: {returns.mean()-returns.std():.4f}')
    ax.set_title(f'{stock_name} 日收益率分布'); ax.set_xlabel('日收益率'); ax.set_ylabel('频数')
    ax.legend(); ax.grid(True); plt.tight_layout()
    plt.savefig(_outp('return_distribution.png'), dpi=150, bbox_inches='tight'); plt.close()

def plot_rsi(df, stock_name):
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(df['trade_date'], df['rsi'], color='blue', linewidth=2)
    ax.axhline(70, color='red', linestyle='--', linewidth=1)
    ax.axhline(30, color='green', linestyle='--', linewidth=1)
    ax.fill_between(df['trade_date'], 70, df['rsi'], where=df['rsi'] >= 70, color='red', alpha=0.2)
    ax.fill_between(df['trade_date'], 30, df['rsi'], where=df['rsi'] <= 30, color='green', alpha=0.2)
    ax.set_title(f'{stock_name} RSI指标图', fontsize=14); ax.set_xlabel('日期'); ax.set_ylabel('RSI值'); ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3); plt.xticks(rotation=45); plt.tight_layout()
    plt.savefig(_outp('rsi_plot.png'), dpi=150, bbox_inches='tight'); plt.close()

def plot_macd(df, stock_name):
    fig, ax1 = plt.subplots(figsize=(14, 7))
    ax1.plot(df['trade_date'], df['macd'], color='blue', linewidth=2, label='MACD')
    ax1.plot(df['trade_date'], df['signal'], color='red', linewidth=2, label='Signal')
    ax1.bar(df['trade_date'], df['histogram'], color='gray', alpha=0.5, label='Histogram')
    ax1.axhline(0, color='black', linestyle='--', linewidth=1)
    ax1.set_title(f'{stock_name} MACD指标图', fontsize=14); ax1.set_xlabel('日期'); ax1.set_ylabel('MACD值')
    ax1.legend(); ax1.grid(True, alpha=0.3); plt.xticks(rotation=45); plt.tight_layout()
    plt.savefig(_outp('macd_plot.png'), dpi=150, bbox_inches='tight'); plt.close()

def plot_bollinger(df, stock_name):
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(df['trade_date'], df['bb_upper'], color='red', linestyle='--', linewidth=1, label='上轨')
    ax.plot(df['trade_date'], df['bb_middle'], color='orange', linewidth=1, label='中轨')
    ax.plot(df['trade_date'], df['bb_lower'], color='green', linestyle='--', linewidth=1, label='下轨')
    ax.plot(df['trade_date'], df['close'], color='blue', linewidth=2, label='收盘价')
    ax.fill_between(df['trade_date'], df['bb_lower'], df['bb_upper'], color='gray', alpha=0.1)
    ax.set_title(f'{stock_name} 布林带指标图', fontsize=14); ax.set_xlabel('日期'); ax.set_ylabel('价格 (元)')
    ax.legend(); ax.grid(True, alpha=0.3); plt.xticks(rotation=45); plt.tight_layout()
    plt.savefig(_outp('bollinger_plot.png'), dpi=150, bbox_inches='tight'); plt.close()

def plot_kline(df, stock_name):
    fig, ax = plt.subplots(figsize=(14, 7))
    x = range(len(df))
    df = df.reset_index(drop=True)
    for i in x:
        color = 'red' if df.loc[i, 'close'] >= df.loc[i, 'open'] else 'green'
        ax.plot([i, i], [df.loc[i, 'low'], df.loc[i, 'high']], color=color, linewidth=1)
        ax.fill_between([i-0.3, i+0.3], df.loc[i, 'open'], df.loc[i, 'close'], color=color)
    ax.set_xticks(range(0, len(df), 10))
    ax.set_xticklabels([df.loc[i, 'trade_date'].strftime('%Y-%m-%d') for i in range(0, len(df), 10)], rotation=45)
    ax.set_title(f'{stock_name} K线图', fontsize=14); ax.set_xlabel('日期'); ax.set_ylabel('价格 (元)')
    ax.grid(True, alpha=0.3); plt.tight_layout()
    plt.savefig(_outp('kline_plot.png'), dpi=150, bbox_inches='tight'); plt.close()

def plot_kdj(df, stock_name):
    fig, ax = plt.subplots(figsize=(16, 8))
    v = df.dropna(subset=['k', 'd', 'j'])
    y_min = min(v['j'].min(), 0) - 10; y_max = max(v['j'].max(), 100) + 10
    ax.plot(v['trade_date'], v['k'], color='blue', linewidth=2, label='K线')
    ax.plot(v['trade_date'], v['d'], color='red', linewidth=2, label='D线')
    ax.plot(v['trade_date'], v['j'], color='green', linewidth=2, label='J线')
    ax.axhline(80, color='red', linestyle='--', linewidth=1, alpha=0.7)
    ax.axhline(20, color='green', linestyle='--', linewidth=1, alpha=0.7)
    ax.axhline(50, color='gray', linestyle=':', linewidth=1, alpha=0.5)
    ax.fill_between(v['trade_date'], 80, v['k'], where=v['k'] >= 80, color='red', alpha=0.1)
    ax.fill_between(v['trade_date'], 20, v['k'], where=v['k'] <= 20, color='green', alpha=0.1)
    ax.set_ylim(y_min, y_max); ax.set_title(f'{stock_name} KDJ指标图', fontsize=14)
    ax.set_xlabel('日期'); ax.set_ylabel('KDJ值'); ax.legend(); ax.grid(True, alpha=0.3)
    ax.set_yticks([0, 20, 50, 80, 100]); plt.xticks(rotation=45); plt.tight_layout()
    plt.savefig(_outp('kdj_plot.png'), dpi=150, bbox_inches='tight'); plt.close()

# ========== 5. 主函数 ==========
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch-only", action="store_true", help="只拉取保存数据，不计算指标不绘图")
    ap.add_argument("--stock", default="平安银行", choices=[s["name"] for s in STOCKS], help="指定用于绘图的股票（默认平安银行）")
    args = ap.parse_args()

    print("=" * 64)
    print("stock_analysis.py  融合版  (AKShare 双源不复权/前复权 + 指标/绘图)")
    print(f"  日期范围: {START} ~ {END}")
    print(f"  股票池: {STOCKS}")
    print(f"  绘图股票: {args.stock}    只拉取: {args.fetch_only}")
    print("=" * 64)

    # 1) 拉取所有股票 不复权+前复权
    all_data = fetch_all()

    # 2) 保存 Excel（双 sheet）+ data.js（嵌套）
    print("\n▶ 保存 Excel 双 sheet...")
    save_excels(all_data)
    print("\n▶ 生成根目录 data.js（供 HTML 看板使用）...")
    build_datajs(all_data)

    if args.fetch_only:
        print("\n✅ --fetch-only：跳过指标计算与绘图。")
        return

    # 3) 指标计算 + 绘图（基于不复权）
    print(f"\n▶ 对 {args.stock}（不复权）计算技术指标...")
    df_nofq = all_data[args.stock]["nofq"].copy()
    df_calc = calculate_indicators(df_nofq)
    print(f"  缺失值：{df_calc.isnull().sum().sum()} 个")
    print(f"  日均收益率：{df_calc['daily_return'].mean():.4f}")
    print(f"  收益率标准差：{df_calc['daily_return'].std():.4f}")
    print(f"  最大涨幅：{df_calc['daily_return'].max():.4f}   最大跌幅：{df_calc['daily_return'].min():.4f}")

    print("\n▶ 绘制 matplotlib PNG 图表（写入 task2/）...")
    plot_close_price(df_calc, args.stock);         print("  ✔ close_price.png")
    plot_return_distribution(df_calc, args.stock); print("  ✔ return_distribution.png")
    plot_rsi(df_calc, args.stock);                 print("  ✔ rsi_plot.png")
    plot_macd(df_calc, args.stock);                print("  ✔ macd_plot.png")
    plot_bollinger(df_calc, args.stock);           print("  ✔ bollinger_plot.png")
    plot_kline(df_calc, args.stock);               print("  ✔ kline_plot.png")
    plot_kdj(df_calc, args.stock);                 print("  ✔ kdj_plot.png")

    print("\n✅ 全部完成！")

if __name__ == '__main__':
    main()
