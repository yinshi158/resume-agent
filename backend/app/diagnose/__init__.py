"""diagnose：JD 诊断（spec §12）。

职责：把 JD 拆成要求清单，逐条在事实源中举证（quote 程序验证），
产出诊断报告。与 M3 validate 共享同一份要求清单（ard/0004）。

依赖方向：fact_store → diagnose（单向，ard/0008）。
举证原则（ard/0003）：找证据，不打分；quote 必须逐字且唯一定位，
找不到可验证证据的一律 gap/nearest——保守偏向（ard/0004）。
"""
