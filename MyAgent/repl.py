#!/usr/bin/env python3

from agentcore import StdoutSink, submit_turn


def main() -> None:
    history = []
    sink = StdoutSink()

    print("MyAgent REPL")
    print("Type /help for commands, /exit to quit.")

    while True:
        try:
            query = input("\n> ")
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print("\nInterrupted. Use /exit to quit.")
            continue

        try:
            should_continue = submit_turn(history, query, sink=sink)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            continue
        except Exception as e:
            print(f"Error: {e}")
            continue

        if not should_continue:
            break


if __name__ == "__main__":
    main()
