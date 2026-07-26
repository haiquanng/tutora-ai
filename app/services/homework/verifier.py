from sympy import (symbols, solve, diff, integrate, limit, simplify,
                   sqrt, log, exp, pi, E, sin, cos, tan, Rational, oo,
                   Matrix, factor, expand)

def verify_answer(solution: dict) -> dict:
    """SymPy verify đáp số. Trả về: {verified, reason}"""
    sympy_code = solution.get("sympy_code")
    if not sympy_code:
        return {"verified": None, "reason": "no_sympy_code"}
    try:
        safe_globals = {
            "__builtins__": {},
            "symbols": symbols, "solve": solve, "diff": diff,
            "integrate": integrate, "limit": limit, "simplify": simplify,
            "sqrt": sqrt, "log": log, "exp": exp, "pi": pi, "E": E,
            "sin": sin, "cos": cos, "tan": tan, "Rational": Rational,
            "oo": oo, "Matrix": Matrix, "factor": factor, "expand": expand
        }
        local_vars = {}
        exec(sympy_code, safe_globals, local_vars)
        result = local_vars.get("answer") or local_vars.get("result")
        return {"verified": True, "sympy_result": str(result), "reason": "sympy_pass"}
    except Exception as e:
        return {"verified": False, "reason": f"sympy_error: {str(e)[:100]}"}
