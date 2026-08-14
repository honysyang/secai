---
name: 智能合约安全审计
description: 用静态分析+符号执行+人工审查检测智能合约常见漏洞
triggers: 智能合约, solidity, evm, blockchain, 区块链, defi, reentrancy, 重入, 合约, nft, dao
---

# 智能合约安全审计

## 目标模式
- `.sol` Solidity 合约文件
- EVM 链上已部署合约地址
- DeFi 协议接口（Uniswap/Aave/Compound）
- NFT 铸造/销毁合约、DAO 治理合约

## 检测方法
1. 静态分析：`slither <target>` 自动检测
2. 符号执行：`myth analyze <target>` 深路径分析
3. 人工审查：重入、访问控制、整数溢出、抢跑、闪电贷

## 常见漏洞类（SWC）
- SWC-107 重入：外部调用先于状态更新 → Checks-Effects-Interactions
- SWC-105 未保护 selfdestruct
- SWC-101 整数溢出：Solidity <0.8.0 未检查算术
- SWC-114 交易顺序依赖（抢跑/三明治）
- SWC-104 未检查调用返回值：`.call()`/`.send()`
- SWC-115 tx.origin 认证（钓鱼风险）
- SWC-106 未保护可升级代理 delegatecall

## 严重性
- 重入导致资金损失 → critical
- 未保护 selfdestruct → critical
- 金融逻辑整数溢出 → high
- 抢跑 → medium
