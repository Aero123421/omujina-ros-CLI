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
        warn("y / n または はい / いいえ で答えてください。")


def ask_text(prompt: str) -> str:
    return input(f"{prompt}\n> ").strip()


def select_from_list(prompt: str, options: Sequence[str]) -> int:
    info(prompt)
    for index, option in enumerate(options, start=1):
        print(f"{index}. {option}")
    while True:
        answer = input("> ").strip()
        if answer.isdigit():
            selected = int(answer)
            if 1 <= selected <= len(options):
                return selected - 1
        warn("番号で選んでください。")


def pause() -> None:
    input("\nEnter を押すとメニューに戻ります。")
