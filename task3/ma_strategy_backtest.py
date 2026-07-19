import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

def load_data(file_path):
    df = pd.read_csv(file_path)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df

def calculate_moving_averages(df, short_period=5, long_period=15):
    df[f'ma{short_period}'] = df['close'].rolling(window=short_period).mean()
    df[f'ma{long_period}'] = df['close'].rolling(window=long_period).mean()
    return df, short_period, long_period

def generate_signals(df, short_period=5, long_period=15):
    df['signal'] = 0
    golden_cross = (df[f'ma{short_period}'] > df[f'ma{long_period}']) & (df[f'ma{short_period}'].shift(1) <= df[f'ma{long_period}'].shift(1))
    death_cross = (df[f'ma{short_period}'] < df[f'ma{long_period}']) & (df[f'ma{short_period}'].shift(1) >= df[f'ma{long_period}'].shift(1))
    df.loc[golden_cross, 'signal'] = 1
    df.loc[death_cross, 'signal'] = -1
    return df

def backtest_strategy(df, initial_capital=100000, commission_rate=0.0003, slippage=0.001):
    df = df.copy()
    df['position'] = 0
    df['cash'] = initial_capital
    df['asset_value'] = initial_capital
    df['shares'] = 0
    df['trade_count'] = 0
    
    position = 0
    cash = initial_capital
    shares = 0
    trade_count = 0
    
    for i in range(1, len(df)):
        if df.loc[i, 'signal'] == 1 and position == 0:
            price = df.loc[i, 'open'] * (1 + slippage)
            max_shares = int(cash / price)
            cost = max_shares * price
            commission = cost * commission_rate
            shares = max_shares
            cash -= cost + commission
            position = 1
            trade_count += 1
        elif df.loc[i, 'signal'] == -1 and position == 1:
            price = df.loc[i, 'open'] * (1 - slippage)
            revenue = shares * price
            commission = revenue * commission_rate
            cash += revenue - commission
            shares = 0
            position = 0
            trade_count += 1
        
        df.loc[i, 'position'] = position
        df.loc[i, 'cash'] = cash
        df.loc[i, 'shares'] = shares
        df.loc[i, 'trade_count'] = trade_count
        df.loc[i, 'asset_value'] = cash + shares * df.loc[i, 'close']
    
    df['strategy_return'] = df['asset_value'].pct_change()
    df['benchmark_return'] = df['close'].pct_change()
    df['cum_strategy_return'] = (1 + df['strategy_return']).cumprod() - 1
    df['cum_benchmark_return'] = (1 + df['benchmark_return']).cumprod() - 1
    
    return df

def calculate_metrics(df):
    valid_df = df.dropna(subset=['strategy_return', 'benchmark_return'])
    
    total_return = df['cum_strategy_return'].iloc[-1]
    benchmark_return = df['cum_benchmark_return'].iloc[-1]
    
    annual_return = (1 + total_return) ** (252 / len(valid_df)) - 1
    
    volatility = valid_df['strategy_return'].std() * np.sqrt(252)
    
    sharpe_ratio = annual_return / volatility if volatility > 0 else 0
    
    max_drawdown = 0
    peak = df['asset_value'].iloc[0]
    for value in df['asset_value']:
        if value > peak:
            peak = value
        drawdown = (peak - value) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    
    win_count = len(valid_df[valid_df['strategy_return'] > 0])
    lose_count = len(valid_df[valid_df['strategy_return'] < 0])
    win_rate = win_count / (win_count + lose_count) if (win_count + lose_count) > 0 else 0
    
    avg_win = valid_df[valid_df['strategy_return'] > 0]['strategy_return'].mean() if win_count > 0 else 0
    avg_lose = abs(valid_df[valid_df['strategy_return'] < 0]['strategy_return'].mean()) if lose_count > 0 else 1
    profit_loss_ratio = avg_win / avg_lose if avg_lose > 0 else 0
    
    trade_count = df['trade_count'].iloc[-1]
    
    metrics = {
        'total_return': total_return,
        'benchmark_return': benchmark_return,
        'annual_return': annual_return,
        'volatility': volatility,
        'sharpe_ratio': sharpe_ratio,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'profit_loss_ratio': profit_loss_ratio,
        'trade_count': trade_count
    }
    
    return metrics

def plot_strategy(df, stock_name, short_period, long_period):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12), sharex=True)
    
    ax1.plot(df['trade_date'], df['close'], color='blue', linewidth=1.5, label='收盘价')
    ax1.plot(df['trade_date'], df[f'ma{short_period}'], color='red', linewidth=1.5, label=f'MA{short_period}')
    ax1.plot(df['trade_date'], df[f'ma{long_period}'], color='orange', linewidth=1.5, label=f'MA{long_period}')
    
    buy_signals = df[df['signal'] == 1]
    sell_signals = df[df['signal'] == -1]
    ax1.scatter(buy_signals['trade_date'], buy_signals['close'], color='green', marker='^', s=100, label='买入信号', zorder=5)
    ax1.scatter(sell_signals['trade_date'], sell_signals['close'], color='red', marker='v', s=100, label='卖出信号', zorder=5)
    
    ax1.set_title(f'{stock_name} 均线策略({short_period}/{long_period})', fontsize=14)
    ax1.set_ylabel('价格 (元)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    ax2.plot(df['trade_date'], df['cum_strategy_return'], color='red', linewidth=2, label='策略收益率')
    ax2.plot(df['trade_date'], df['cum_benchmark_return'], color='blue', linewidth=2, label='基准收益率')
    ax2.fill_between(df['trade_date'], df['cum_strategy_return'], df['cum_benchmark_return'], 
                     where=df['cum_strategy_return'] > df['cum_benchmark_return'], color='red', alpha=0.1)
    ax2.fill_between(df['trade_date'], df['cum_strategy_return'], df['cum_benchmark_return'], 
                     where=df['cum_strategy_return'] < df['cum_benchmark_return'], color='blue', alpha=0.1)
    ax2.set_title('累计收益率对比', fontsize=14)
    ax2.set_xlabel('日期')
    ax2.set_ylabel('累计收益率')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig('ma_strategy_plot.png', dpi=150, bbox_inches='tight')
    plt.close()

def plot_sharpe_ratio(df, stock_name, metrics):
    rolling_window = 60
    
    df['rolling_mean'] = df['strategy_return'].rolling(window=rolling_window).mean() * 252
    df['rolling_std'] = df['strategy_return'].rolling(window=rolling_window).std() * np.sqrt(252)
    df['rolling_sharpe'] = df['rolling_mean'] / df['rolling_std']
    
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(16, 15), sharex=True)
    
    ax1.plot(df['trade_date'], df['rolling_sharpe'], color='purple', linewidth=2, label='滚动夏普比率')
    ax1.axhline(0, color='gray', linestyle='--', linewidth=1)
    ax1.axhline(1, color='green', linestyle='--', linewidth=1, alpha=0.7, label='参考值1')
    ax1.axhline(-1, color='red', linestyle='--', linewidth=1, alpha=0.7, label='参考值-1')
    ax1.fill_between(df['trade_date'], df['rolling_sharpe'], 0, 
                     where=df['rolling_sharpe'] > 0, color='green', alpha=0.1)
    ax1.fill_between(df['trade_date'], df['rolling_sharpe'], 0, 
                     where=df['rolling_sharpe'] < 0, color='red', alpha=0.1)
    ax1.set_title(f'{stock_name} 滚动夏普比率({rolling_window}日窗口)', fontsize=14)
    ax1.set_ylabel('夏普比率')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    ax2.plot(df['trade_date'], df['rolling_mean'], color='blue', linewidth=2, label='滚动年化收益')
    ax2.axhline(0, color='gray', linestyle='--', linewidth=1)
    ax2.set_title('滚动年化收益率', fontsize=14)
    ax2.set_ylabel('年化收益率')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    ax3.plot(df['trade_date'], df['rolling_std'], color='orange', linewidth=2, label='滚动年化波动')
    ax3.set_title('滚动年化波动率', fontsize=14)
    ax3.set_xlabel('日期')
    ax3.set_ylabel('年化波动率')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig('sharpe_ratio_plot.png', dpi=150, bbox_inches='tight')
    plt.close()
    
    fig2, ax = plt.subplots(figsize=(10, 8))
    metrics_data = {
        '指标': ['总收益率', '年化收益率', '年化波动率', '夏普比率', '最大回撤', '胜率', '盈亏比'],
        '策略': [
            metrics['total_return'],
            metrics['annual_return'],
            metrics['volatility'],
            metrics['sharpe_ratio'],
            metrics['max_drawdown'],
            metrics['win_rate'],
            metrics['profit_loss_ratio']
        ]
    }
    df_metrics = pd.DataFrame(metrics_data)
    
    colors = ['blue' if x >= 0 else 'red' for x in df_metrics['策略']]
    bars = ax.bar(df_metrics['指标'], df_metrics['策略'], color=colors)
    
    for bar, value in zip(bars, df_metrics['策略']):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2, height,
                f'{value:.2%}' if abs(value) < 2 else f'{value:.2f}',
                ha='center', va='bottom', fontsize=12)
    
    ax.set_title(f'{stock_name} 策略绩效指标', fontsize=14)
    ax.set_ylabel('值')
    ax.grid(True, alpha=0.3, axis='y')
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig('metrics_bar_plot.png', dpi=150, bbox_inches='tight')
    plt.close()

def main():
    data_path = '../task2/平安银行.csv'
    stock_name = '平安银行'
    
    print(f"加载{stock_name}数据...")
    df = load_data(data_path)
    print(f"数据范围：{df['trade_date'].min()} ~ {df['trade_date'].max()}")
    print(f"数据条数：{len(df)}")
    
    short_period = 5
    long_period = 15
    
    print(f"\n计算均线(MA{short_period}/{long_period})...")
    df, short_period, long_period = calculate_moving_averages(df, short_period, long_period)
    
    print("生成交易信号...")
    df = generate_signals(df, short_period, long_period)
    
    print("回测策略...")
    df = backtest_strategy(df)
    
    print("\n计算量化指标...")
    metrics = calculate_metrics(df)
    
    print("\n" + "="*50)
    print("回测结果")
    print("="*50)
    print(f"策略总收益率: {metrics['total_return']:.2%}")
    print(f"基准总收益率: {metrics['benchmark_return']:.2%}")
    print(f"年化收益率: {metrics['annual_return']:.2%}")
    print(f"年化波动率: {metrics['volatility']:.2%}")
    print(f"夏普比率: {metrics['sharpe_ratio']:.2f}")
    print(f"最大回撤: {metrics['max_drawdown']:.2%}")
    print(f"胜率: {metrics['win_rate']:.2%}")
    print(f"盈亏比: {metrics['profit_loss_ratio']:.2f}")
    print(f"交易次数: {metrics['trade_count']}")
    print("="*50)
    
    print("\n绘制可视化图表...")
    plot_strategy(df, stock_name, short_period, long_period)
    plot_sharpe_ratio(df, stock_name, metrics)
    
    df.to_csv('ma_strategy_results.csv', index=False, encoding='utf-8-sig')
    print("\n结果已保存为 ma_strategy_results.csv")
    print("图表已保存为 ma_strategy_plot.png")
    print("图表已保存为 sharpe_ratio_plot.png")
    print("图表已保存为 metrics_bar_plot.png")

if __name__ == '__main__':
    main()