"""Generate Doxygen-style comments for C++ functions using Ollama.

The generator calls :meth:`OllamaTranslator.generate` with a focused
system prompt and returns a clean ``/** ... */`` block that can be
inserted directly above the function declaration.
"""

from __future__ import annotations

from .header_parser import FunctionInfo
from .translator import OllamaTranslator

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a C++ documentation expert. Generate a Doxygen comment for the given function.

Rules:
1. Use /** ... */ format with lines prefixed by " * ".
2. @brief — one-line description (required).
3. @param name description — one line per parameter; omit if no parameters.
4. @return description — include only if the return type is not void, not a constructor/destructor.
5. For constructors: describe what is initialised or created.
6. For destructors: write exactly "Destroys the object and releases resources."
7. Infer behavior solely from the function/parameter names and types.
8. Be concise. Do NOT include implementation guesses.
9. Output ONLY the comment block — no markdown fences, no explanations.

Example output:
/**
 * @brief Computes the bounding box enclosing all visible elements.
 * @param elements List of renderable scene objects.
 * @return Axis-aligned bounding box in world space.
 */\
"""


# ── Public API ────────────────────────────────────────────────────────────────

def generate_doxygen(
    func: FunctionInfo,
    translator: OllamaTranslator,
    chunk_callback=None,
) -> str:
    """Generate a ``/** ... */`` Doxygen comment for *func*.

    Parameters
    ----------
    func:
        Function metadata from :func:`~translate_comments.header_parser.parse_header`.
    translator:
        Configured :class:`~translate_comments.translator.OllamaTranslator`.
    chunk_callback:
        Optional ``(partial_text: str) -> None`` called as tokens stream in.

    Returns
    -------
    str
        A clean Doxygen comment block, ready to be inserted above the function.
    """
    ctx = f"This function is a member of class '{func.class_context}'.\n\n" if func.class_context else ""
    prompt = (
        f"{ctx}"
        f"Function declaration:\n"
        f"```cpp\n{func.full_signature}\n```\n\n"
        f"Generate the Doxygen comment."
    )

    result = translator.generate(prompt, system=_SYSTEM, chunk_callback=chunk_callback)

    # Strip any markdown code fences the model may have added
    result = result.strip()
    lines = result.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    result = "\n".join(lines).strip()

    # Ensure the block starts with /**
    if not result.startswith("/**") and not result.startswith("/*"):
        result = f"/**\n * {result}\n */"

    return result
