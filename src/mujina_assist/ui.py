from __future__ import annotations

from typing import Sequence


RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def _paint(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def title(text: str) -> None:
    print(_paint(f"\n{text}", BOLD + CYAN))


def section(text: str) -> None:
    print(_paint(f"\n[{text}]", BOLD))


def info(text: str) -> None:
    print(text)


def success(text: str) -> None:
    print(_paint(text, GREEN))


def warn(text: str) -> None:
    print(_paint(text, YELLOW))


def error(text: str) -> None:
    print(_paint(text, RED))


def bullet(text: str) -> None:
    print(f"- {text}")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{prompt} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes", "ye", "はい", "hai", "h"}:
            return True
        if answer in {"n", "no", "いいえ", "iie"}:
            return False
        warn("`y` / `n`、または `はい` / `いいえ` で答えてください。")


def ask_text(prompt: str) -> str:
    return input(f"{prompt}\n> ").strip()


def select_from_list(
    prompt: str,
    options: Sequence[str],
    *,
    allow_back: bool = False,
    allow_cancel: bool = False,
) -> int | None:
    info(prompt)
    if allow_back:
        print("0. 戻る")
    for index, option in enumerate(options, start=1):
        print(f"{index}. {option}")
    if allow_cancel:
        print("q. 中止")
    while True:
        answer = input("> ").strip().lower()
        if allow_back and answer in {"0", "b", "back"}:
            return None
        if allow_cancel and answer in {"q", "quit", "cancel"}:
            return None
        if answer.isdigit():
            selected = int(answer)
            if 1 <= selected <= len(options):
                return selected - 1
        if allow_back and allow_cancel:
            warn("番号で選んでください。戻るなら `0`、中止なら `q` です。")
        elif allow_back:
            warn("番号で選んでください。戻るなら `0` です。")
        elif allow_cancel:
            warn("番号で選んでください。中止なら `q` です。")
        else:
            warn("番号で選んでください。")


def pause(message: str = "Enter を押すとメニューに戻ります。") -> None:
    input(f"\n{message}")
