STRING_HTML_MAP = {"<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;"}
HTML_STRING_MAP = {
    "&nbsp;": " ",
    "&quot;": '"',
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
}

_number_emojis = [
    "0️⃣",
    "1️⃣",
    "2️⃣",
    "3️⃣",
    "4️⃣",
    "5️⃣",
    "6️⃣",
    "7️⃣",
    "8️⃣",
    "9️⃣",
    "🔟",
]


class _NumberEmojis(object):
    def __getitem__(self, item: int) -> str:
        if not isinstance(item, int):
            raise TypeError(f"Need 'int', not '{type(item).__name__}'")
        if item < 0:
            raise ValueError(f"Must be a positive integer, not '{item}'")
        if item < len(_number_emojis):
            return _number_emojis[item]
        else:
            result = str(item)
            for k, v in enumerate(_number_emojis):
                result = result.replace(str(k), v)
            return result


number_emojis = _NumberEmojis()
