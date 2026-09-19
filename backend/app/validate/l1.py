"""L1 衍生数字校验（formula 白名单求值；spec §16.4，ard/0005 / 已知问题 P2）。

- **白名单 AST**：只允许数字、四则运算、括号、百分号字面量；其余节点
  （名称/调用/属性/下标/比较/幂/字符串…）一律拒绝；**禁用 eval**；
- **重算而非信声明**（R6）：formula 求值结果按句面 value 的呈现精度
  舍入后须与 value 相等（"30%" ↔ (910000-700000)/700000 按百分位舍入）；
  source 数值按其原始文本精度参与比对（"91 万"精确到万位）；
- **区间形式两个端点分别求值**：value 形如 "30%~35%" 时 formula 用
  "~"（或"至"）分列两段子式，端点按顺序一一比对；
- formula 中的数字字面量必须可回溯到该衍生数字声明的出处（换算常数
  1 / 100 除外），否则视为偷渡；
- 模糊量化词（"近翻倍/约两倍/百余"）：不得引入出处没有的，除非有
  数值（衍生结果或出处数字）落在该词的映射区间内。
"""

from __future__ import annotations

import ast
import re

from .l0 import NumberToken, _close, extract_numbers
from .schemas import DerivedNumber, LayerVerdict, ValidationFact

# ---------------------------------------------------------------------------
# formula 白名单求值器
# ---------------------------------------------------------------------------

_FORMULA_MAX_LEN = 200

_ALLOWED_BINOPS: dict[type, str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
}
_ALLOWED_UNARYOPS: dict[type, str] = {ast.UAdd: "+", ast.USub: "-"}

# 百分比字面量（"30%"）→ "(30/100)"（AST 无百分号节点；不是取模运算）
_PERCENT_LITERAL_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%")


class FormulaError(ValueError):
    """formula 非法（语法/白名单外节点/除零等）。"""


def _preprocess(formula: str) -> str:
    return _PERCENT_LITERAL_RE.sub(r"(\1/100)", formula)


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise FormulaError(f"不允许的字面量：{node.value!r}（只允许数字）")
        return float(node.value)
    if isinstance(node, ast.BinOp):
        op = type(node.op)
        if op not in _ALLOWED_BINOPS:
            raise FormulaError(f"不允许的运算符：{op.__name__}（白名单：+ - * /）")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if op is ast.Add:
            return left + right
        if op is ast.Sub:
            return left - right
        if op is ast.Mult:
            return left * right
        if right == 0:
            raise FormulaError("除数为零")
        return left / right
    if isinstance(node, ast.UnaryOp):
        op = type(node.op)
        if op not in _ALLOWED_UNARYOPS:
            raise FormulaError(f"不允许的一元运算符：{op.__name__}")
        operand = _eval_node(node.operand)
        return operand if op is ast.UAdd else -operand
    raise FormulaError(
        f"不允许的语法节点：{type(node).__name__}"
        f"（白名单：数字、四则运算、括号、百分号字面量）"
    )


def parse_formula(formula: str) -> ast.AST:
    """解析 formula 为 AST（不做求值；白名单外节点在此阶段不拒绝）。"""
    if not isinstance(formula, str) or not formula.strip():
        raise FormulaError("formula 为空")
    text = formula.strip()
    if len(text) > _FORMULA_MAX_LEN:
        raise FormulaError(f"formula 过长（>{_FORMULA_MAX_LEN} 字符）")
    try:
        return ast.parse(_preprocess(text), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"formula 语法错误：{exc.msg}") from None


def evaluate_formula(formula: str) -> float:
    """白名单求值（禁用 eval；拒绝一切白名单外节点）。"""
    return _eval_node(parse_formula(formula))


def formula_literals(formula: str) -> list[float]:
    """formula 中的全部数字字面量（含百分号字面量换算前的原值）。"""
    tree = parse_formula(formula)
    literals: list[float] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            value = float(node.value)
            # 百分号字面量预处理后为 (n/100)：还原为 n 参与出处比对
            literals.append(value)
    # 还原百分号：_preprocess 把 "30%" 写成 (30/100)，walk 会看到 30 与 100 两个常量，
    # 其中 100 是换算产物（白名单内）；无法区分时保守保留全部字面量。
    return literals


# ---------------------------------------------------------------------------
# 端点解析与比对
# ---------------------------------------------------------------------------

_RANGE_SPLIT_RE = re.compile(r"[~～]|至")


def _split_endpoints(text: str) -> list[str]:
    return [part.strip() for part in _RANGE_SPLIT_RE.split(text or "") if part.strip()]


def _parse_value_endpoints(value: str) -> list[NumberToken] | None:
    """value → 1~2 个数字端点；解析失败返回 None。"""
    parts = _split_endpoints(value)
    if not parts or len(parts) > 2:
        return None
    endpoints: list[NumberToken] = []
    for part in parts:
        tokens = extract_numbers(part)
        if len(tokens) != 1:
            return None
        endpoints.append(tokens[0])
    return endpoints


# 换算常数白名单：百分比换算（*100）与恒等（*1）
_CONST_WHITELIST = frozenset({1.0, 100.0})

_UNIT_SCALE = {"万": 10_000, "亿": 100_000_000}


def _literal_sourced(literal: float, source_numbers: list[tuple[float, str]]) -> bool:
    """字面量可回溯到出处数字（按单位换算表双向核对，R6"原始文本精度"）。

    不含换算常数白名单（1 / 100 / 万·亿因子）——白名单在
    ``_formula_grounded`` 中单独排除，不参与"是否由出处数字推导"判定。
    """
    for value, unit in source_numbers:
        if _close(literal, value):
            return True
        scale = _UNIT_SCALE.get(unit)
        if scale and (_close(literal * scale, value) or _close(literal / scale, value)):
            return True
        # 出处是百分比（30%）而 formula 直接用小数比值（0.3）的约定
        if unit == "%" and _close(literal, value / 100):
            return True
    return False


def _formula_grounded(
    formula: str, source_numbers: list[tuple[float, str]]
) -> tuple[bool, list[float]]:
    """formula 是否由出处数字推导（判定 + 未回溯的字面量清单）。

    纪律：formula 至少要有 **一个** 字面量可回溯到声明出处的数字——
    纯常数公式（如直接写 "0.3" 或 "(3+4)/7"）一律拒绝（那正是偷渡数字
    的另一种写法）。其余字面量作为运算常数放行（如 /2、*12 这类换算），
    避免把正常算式误伤成失败；1 / 100 与出处的万/亿换算因子（10000 等）
    始终放行。
    """
    literals = formula_literals(formula)
    unsourced: list[float] = []
    grounded = False
    scale_consts = {
        float(_UNIT_SCALE[unit]) for _, unit in source_numbers if unit in _UNIT_SCALE
    }
    for literal in literals:
        if literal in _CONST_WHITELIST or literal in scale_consts:
            continue  # 换算常数：放行，但不计为"由出处数字推导"
        if _literal_sourced(literal, source_numbers):
            grounded = True
        else:
            unsourced.append(literal)
    return grounded, unsourced


def _match_endpoint(result: float, token: NumberToken) -> bool:
    """求值结果按端点呈现精度舍入后与端点相等（百分比双约定都接受）。"""
    decimals = token.decimals
    if _close(round(result, decimals), token.display):
        return True
    if token.scale and _close(round(result, decimals), token.value):
        return True
    if token.unit == "%" and _close(round(result * 100, decimals), token.display):
        return True
    return False


# ---------------------------------------------------------------------------
# 模糊量化词（映射区间；v1 覆盖常见比值/数量词，随真实语料迭代）
# ---------------------------------------------------------------------------

_FUZZY_INTERVALS: tuple[tuple[str, tuple[float, float]], ...] = (
    ("接近翻倍", (1.8, 2.0)),
    ("近翻倍", (1.8, 2.0)),
    ("约两倍", (1.8, 2.2)),
    ("近两倍", (1.8, 2.0)),
    ("两倍", (1.9, 2.1)),
    ("翻倍", (1.9, 2.1)),
    ("三倍", (2.9, 3.1)),
    ("百余", (100.0, 199.0)),
    ("数十", (20.0, 99.0)),
)


def _fuzzy_candidates(
    computed: list[float],
    source_numbers: list[tuple[float, str]],
) -> list[float]:
    candidates = list(computed)
    for value, unit in source_numbers:
        candidates.append(value)
        if unit == "%":
            candidates.append(1 + value / 100)  # "增长 97%" 作为倍数 1.97 参与
    return candidates


def _check_fuzzy(
    sentence_text: str,
    *,
    computed: list[float],
    source_facts: list[ValidationFact],
) -> list[dict]:
    """模糊量化词检查：出处本身含该词，或存在数值落在映射区间内。"""
    failures: list[dict] = []
    source_numbers: list[tuple[float, str]] = []
    for fact in source_facts:
        source_numbers.extend(fact.numbers)
    candidates = _fuzzy_candidates(computed, source_numbers)
    for word, (low, high) in _FUZZY_INTERVALS:
        if word not in sentence_text:
            continue
        if any(word in fact.raw_quote for fact in source_facts):
            continue  # 出处原文就有该词，不算"引入"
        if any(low <= value <= high for value in candidates):
            continue
        failures.append(
            {
                "kind": "fuzzy",
                "word": word,
                "interval": [low, high],
                "message": (
                    f"模糊量化词「{word}」在出处中没有对应数值支撑"
                    f"（映射区间 {low:g}~{high:g}）"
                ),
            }
        )
    return failures


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def check_l1(
    sentence_text: str,
    *,
    source_fact_ids: list[str],
    facts_by_id: dict[str, ValidationFact],
    derived_numbers: list[DerivedNumber],
) -> LayerVerdict:
    """L1 判定：每个衍生数字求值 + 重算比对 + 字面量溯源 + 模糊量化词。"""
    failures: list[dict] = []
    computed: list[float] = []

    for index, derived in enumerate(derived_numbers):
        label = f"衍生数字 #{index + 1}（{derived.value}）"

        # 出处声明校验：非空、属于本句出处、全部存在
        if not derived.source_ids:
            failures.append(
                {"kind": "source", "message": f"{label}：未声明参与推导的出处（source_ids 为空）"}
            )
            continue
        unknown = [fid for fid in derived.source_ids if fid not in facts_by_id]
        if unknown:
            failures.append(
                {"kind": "source", "message": f"{label}：出处条目不存在：{unknown}"}
            )
            continue
        outside = [fid for fid in derived.source_ids if fid not in source_fact_ids]
        if outside:
            failures.append(
                {
                    "kind": "source",
                    "message": f"{label}：出处 {outside} 不属于该句声明的 source_fact_ids",
                }
            )
            continue

        formulas = _split_endpoints(derived.formula)
        endpoints = _parse_value_endpoints(derived.value)
        if endpoints is None:
            failures.append(
                {"kind": "value", "message": f"{label}：value 无法解析为数字端点：{derived.value!r}"}
            )
            continue
        if len(formulas) != len(endpoints):
            failures.append(
                {
                    "kind": "shape",
                    "message": (
                        f"{label}：区间形式两端点分列——value 有 {len(endpoints)} 个端点，"
                        f"formula 有 {len(formulas)} 段（用 ~ 分隔）"
                    ),
                }
            )
            continue

        source_numbers: list[tuple[float, str]] = []
        for fact_id in derived.source_ids:
            source_numbers.extend(facts_by_id[fact_id].numbers)

        for position, (formula_part, endpoint) in enumerate(zip(formulas, endpoints, strict=True)):
            try:
                result = evaluate_formula(formula_part)
            except FormulaError as exc:
                failures.append({"kind": "formula", "message": f"{label}：{exc}"})
                continue
            grounded, unsourced = _formula_grounded(formula_part, source_numbers)
            if not grounded:
                failures.append(
                    {
                        "kind": "literal",
                        "message": (
                            f"{label}：formula 中的数字 {[f'{v:g}' for v in unsourced]} "
                            f"均不在所选出处的数字中（公式必须由出处数字推导，来源：{derived.source_ids}）"
                        ),
                    }
                )
            if not _match_endpoint(result, endpoint):
                failures.append(
                    {
                        "kind": "recompute",
                        "message": (
                            f"{label}：重算 {result:.6g} 与句面值 {endpoint.raw.strip()!r} "
                            f"不相等（按呈现精度舍入后比对）"
                        ),
                    }
                )
            else:
                computed.append(result)

    computed.extend(_recompute_ok_values(derived_numbers, facts_by_id))

    source_facts = [facts_by_id[fid] for fid in source_fact_ids if fid in facts_by_id]
    failures.extend(
        _check_fuzzy(sentence_text, computed=computed, source_facts=source_facts)
    )

    if failures:
        first = failures[0]["message"]
        extra = "" if len(failures) == 1 else f"（共 {len(failures)} 处）"
        return LayerVerdict(
            layer="L1",
            verdict="fail",
            detail={"message": first + extra, "failures": failures},
        )
    return LayerVerdict(
        layer="L1",
        verdict="pass",
        detail={
            "message": (
                "衍生数字求值通过" if derived_numbers else "无衍生数字，模糊量化词无异常"
            ),
            "derived_count": len(derived_numbers),
        },
    )


def _recompute_ok_values(
    derived_numbers: list[DerivedNumber],
    facts_by_id: dict[str, ValidationFact],
) -> list[float]:
    """模糊量化词判定复用：产出所有可求值 formula 的结果（求值失败跳过）。"""
    values: list[float] = []
    for derived in derived_numbers:
        for part in _split_endpoints(derived.formula):
            try:
                values.append(evaluate_formula(part))
            except FormulaError:
                continue
    return values
