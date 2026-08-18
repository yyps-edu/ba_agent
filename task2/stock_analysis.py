"""
stock_analysis.py — 股票技术指标计算 + matplotlib 可视化

功能：
  1) 从根目录 stock_data.xlsx 的 "{股票名}_不复权" sheet 读取数据（fetch_data.py 生成）
  2) 计算技术指标：RSI / MACD / 布林带 / KDJ
  3) matplotlib 绘图（收盘价/收益率分布/RSI/MACD/布林带/K线/KDJ）

命令行：
  python task2/stock_analysis.py                    # 默认对平安银行绘图
  python task2/stock_analysis.py --stock 恒瑞医药   # 指定股票
"""
import os, sys, argparse, warnings
warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXCEL_NAME = "stock_data.xlsx"

STOCKS = [
    {"name": "恒瑞医药", "symbol": "600276"},
    {"name": "平安银行", "symbol": "000001"},
]

# ========== 1. 从根目录 stock_data.xlsx 读取不复权 sheet ==========
def load_from_excel(stock_name, adjust="不复权"):
    path = os.path.join(ROOT, EXCEL_NAME)
    sheet = f"{stock_name}_{adjust}"
    if not os.path.exists(path):
        print(f"❌ 找不到 {path}，请先运行 python fetch_data.py 获取数据")
        sys.exit(1)
    df = pd.read_excel(path, sheet_name=sheet)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    if "daily_return" not in df.columns or df["daily_return"].isnull().all():
        df["daily_return"] = df["close"].pct_change()
    print(f"  ✔ 读取 {EXCEL_NAME} / sheet={sheet}  {len(df)} 行")
    return df

# ========== 2. 指标计算 ==========
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

# ========== 3. matplotlib 绘图 ==========
def _outp(name):
    return os.path.join(HERE, name)

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

# ========== 4. 主函数 ==========
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", default="平安银行", choices=[s["name"] for s in STOCKS], help="指定用于绘图的股票（默认平安银行）")
    args = ap.parse_args()

    print("=" * 64)
    print("stock_analysis.py  指标计算 + 可视化（从 stock_data.xlsx 读取）")
    print(f"  绘图股票: {args.stock}")
    print("=" * 64)

    print(f"\n▶ 读取 {EXCEL_NAME} / {args.stock}_不复权 ...")
    df = load_from_excel(args.stock)

    print(f"\n▶ 对 {args.stock}（不复权）计算技术指标...")
    df_calc = calculate_indicators(df)
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
