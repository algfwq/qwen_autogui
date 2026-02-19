import json
import base64
import time
import io
import subprocess
import sys
import ctypes
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import mss
import mss.tools
import pyautogui
import pyperclip
from PIL import Image
from openai import OpenAI

system_prompt = """你是一个电脑操作助手。你的任务是根据用户的指令，通过一系列操作来完成用户的任务。

每次响应必须返回一个 JSON 对象，格式如下：
{
    "thought": "你的思考过程，分析当前屏幕状态和下一步该做什么",
    "action": "动作类型",
    "parameters": {
        "参数 1": "值 1",
        "参数 2": "值 2"
    }
}

可用的动作类型：
- click: 点击，参数：x, y (0-1000 的归一化坐标)
- double_click: 双击，参数：x, y
- right_click: 右键点击，参数：x, y
- type: 输入文本，参数：text (要输入的文本)
- press: 按键，参数：keys (按键数组，如 ["ctrl", "c"])
- scroll: 滚动，参数：amount (滚动量), x, y (可选，滚动位置)
- drag: 拖拽，参数：start_x, start_y, end_x, end_y, duration (可选)
- move: 移动鼠标，参数：x, y, duration (可选)
- wait: 等待，参数：seconds
- run_command: 运行终端命令，参数：command (命令字符串), shell (可选，默认 true), timeout (可选，超时秒数，默认 30)
- task_complete: 任务完成，参数：result (任务结果描述), summary (可选，任务总结，包括执行步骤和最终答案)

注意：
1. 坐标系统使用 1000x1000 的归一化坐标，(0,0) 是左上角，(1000,1000) 是右下角
2. 每次只执行一个动作
3. 仔细观察屏幕内容，做出合理的决策
4. 如果任务完成，使用 task_complete 动作，并在 summary 中提供完整的任务总结或用户需要的答案
5. 如果遇到困难，尝试不同的方法
6. VSCode 打开控制台的快捷键是 ctrl+shift+`
7. window 电脑用 Set-Content -Encoding utf8 文件名 "内容" 来写文件
8. task_complete 的 summary 字段应包含：执行的主要步骤、最终结果、以及用户需要的具体答案（如果有）
"""


@dataclass
class Action:
    action_type: str
    parameters: Dict[str, Any]
    thought: str


class ScreenAgent:
    def __init__(self, config_path: str = "config.json"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        api_config = self.config["api"]
        self.client = OpenAI(
            base_url=api_config["base_url"],
            api_key=api_config["api_key"]
        )
        self.model = api_config["model"]
        self.max_tokens = api_config["max_tokens"]
        self.temperature = api_config["temperature"]

        self.max_iterations = self.config["agent"]["max_iterations"]
        self.delay = self.config["agent"]["delay_between_actions"]
        pyautogui.PAUSE = 0.1
        pyautogui.FAILSAFE = False

        self.screen_width, self.screen_height = pyautogui.size()
        print(f"Screen resolution: {self.screen_width}x{self.screen_height}")
        if self.max_iterations == -1:
            print("Max iterations: unlimited")
        else:
            print(f"Max iterations: {self.max_iterations}")

        self.conversation_history: List[Dict[str, Any]] = []
        self.task_summary: Optional[str] = None
    
    def _check_admin_privilege(self) -> bool:
        """检查是否以管理员权限运行"""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    def capture_screen(self) -> str:
        """截取屏幕并返回 base64 编码的图片"""
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)

            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85, optimize=True)
            img_data = buffer.getvalue()

            return base64.b64encode(img_data).decode("utf-8")
    
    def map_coordinates(self, x: float, y: float) -> tuple[int, int]:
        """将模型返回的 1000x1000 坐标映射到实际屏幕分辨率"""
        real_x = int(x / 1000 * self.screen_width)
        real_y = int(y / 1000 * self.screen_height)
        return real_x, real_y

    def execute_action(self, action: Action) -> str:
        """执行 AI 返回的动作"""
        action_type = action.action_type.lower()
        params = action.parameters

        try:
            if action_type == "click":
                x, y = self.map_coordinates(params.get("x", 500), params.get("y", 500))
                x, y = max(0, min(x, self.screen_width - 1)), max(0, min(y, self.screen_height - 1))
                pyautogui.moveTo(x, y, duration=0.3)
                time.sleep(0.1)
                pyautogui.click(button='left')
                time.sleep(0.1)
                return f"Clicked at ({x}, {y})"

            elif action_type == "double_click":
                x, y = self.map_coordinates(params.get("x", 500), params.get("y", 500))
                x, y = max(0, min(x, self.screen_width - 1)), max(0, min(y, self.screen_height - 1))
                pyautogui.moveTo(x, y, duration=0.3)
                time.sleep(0.1)
                pyautogui.doubleClick(button='left')
                time.sleep(0.1)
                return f"Double clicked at ({x}, {y})"

            elif action_type == "right_click":
                x, y = self.map_coordinates(params.get("x", 500), params.get("y", 500))
                x, y = max(0, min(x, self.screen_width - 1)), max(0, min(y, self.screen_height - 1))
                pyautogui.moveTo(x, y, duration=0.3)
                time.sleep(0.1)
                pyautogui.rightClick()
                time.sleep(0.1)
                return f"Right clicked at ({x}, {y})"

            elif action_type == "type":
                text = params.get("text", "")
                old_clipboard = pyperclip.paste()
                try:
                    pyperclip.copy(text)
                    time.sleep(0.1)
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.1)
                finally:
                    pyperclip.copy(old_clipboard)
                return f"Typed: {text}"

            elif action_type == "press":
                keys = params.get("keys", [])
                if isinstance(keys, str):
                    keys = [keys]
                pyautogui.hotkey(*keys)
                return f"Pressed: {'+'.join(keys)}"

            elif action_type == "scroll":
                amount = params.get("amount", 100)
                x = params.get("x")
                y = params.get("y")
                if x is not None and y is not None:
                    x, y = self.map_coordinates(x, y)
                    pyautogui.scroll(amount, x=x, y=y)
                else:
                    pyautogui.scroll(amount)
                return f"Scrolled: {amount}"

            elif action_type == "drag":
                start_x, start_y = self.map_coordinates(params.get("start_x", 500), params.get("start_y", 500))
                end_x, end_y = self.map_coordinates(params.get("end_x", 500), params.get("end_y", 500))
                start_x, start_y = max(0, min(start_x, self.screen_width - 1)), max(0, min(start_y, self.screen_height - 1))
                end_x, end_y = max(0, min(end_x, self.screen_width - 1)), max(0, min(end_y, self.screen_height - 1))
                duration = params.get("duration", 1.0)
                pyautogui.moveTo(start_x, start_y)
                pyautogui.mouseDown()
                pyautogui.moveTo(end_x, end_y, duration=duration)
                pyautogui.mouseUp()
                time.sleep(0.1)
                return f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})"

            elif action_type == "move":
                x, y = self.map_coordinates(params.get("x", 500), params.get("y", 500))
                x, y = max(0, min(x, self.screen_width - 1)), max(0, min(y, self.screen_height - 1))
                duration = params.get("duration", 0.5)
                pyautogui.moveTo(x=x, y=y, duration=duration)
                time.sleep(0.1)
                return f"Moved to ({x}, {y})"

            elif action_type == "wait":
                seconds = params.get("seconds", 1.0)
                time.sleep(seconds)
                return f"Waited for {seconds} seconds"

            elif action_type == "run_command":
                command = params.get("command", "")
                shell = params.get("shell", True)
                timeout = params.get("timeout", 30)
                try:
                    result = subprocess.run(
                        command,
                        shell=shell,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        encoding="utf-8",
                        errors="replace"
                    )
                    output = result.stdout.strip()
                    error = result.stderr.strip()
                    return_code = result.returncode
                    response = f"Command: {command}\nReturn code: {return_code}"
                    if output:
                        response += f"\nOutput:\n{output}"
                    if error:
                        response += f"\nError:\n{error}"
                    return response
                except subprocess.TimeoutExpired:
                    return f"Command timed out after {timeout} seconds: {command}"
                except Exception as e:
                    return f"Error executing command: {str(e)}"

            elif action_type == "task_complete":
                result = params.get("result", "Task completed")
                summary = params.get("summary", result)
                self.task_summary = summary
                return f"Result: {result}\nSummary: {summary}"

            else:
                return f"Unknown action type: {action_type}"

        except Exception as e:
            return f"Error executing action: {str(e)}"

    def parse_action(self, response_text: str) -> Optional[Action]:
        """解析 AI 返回的动作"""
        try:
            import re
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                action_json = json.loads(json_match.group())
                return Action(
                    action_type=action_json.get("action", "wait"),
                    parameters=action_json.get("parameters", {}),
                    thought=action_json.get("thought", "")
                )
        except Exception as e:
            print(f"Error parsing action: {e}")
        return None

    def run(self, task: str) -> str:
        """运行 Agent 完成用户任务"""

        self.conversation_history = [
            {"role": "system", "content": system_prompt}
        ]

        print(f"\n{'='*60}")
        print(f"Starting task: {task}")
        print(f"{'='*60}\n")

        iteration = 0
        while self.max_iterations == -1 or iteration < self.max_iterations:
            iteration += 1
            if self.max_iterations != -1:
                print(f"\n--- Iteration {iteration}/{self.max_iterations} ---")
            else:
                print(f"\n--- Iteration {iteration} ---")

            screenshot_base64 = self.capture_screen()
            print("Captured screenshot")

            user_message = {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"当前任务：{task}\n请分析屏幕并决定下一步操作。"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{screenshot_base64}"
                        }
                    }
                ]
            }

            messages = self.conversation_history + [user_message]

            print("Sending to AI...")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature
            )

            ai_response = response.choices[0].message.content
            print(f"AI response:\n{ai_response}\n")

            self.conversation_history.append(user_message)
            self.conversation_history.append({
                "role": "assistant",
                "content": ai_response
            })

            action = self.parse_action(ai_response)
            if action is None:
                print("Failed to parse action, retrying...")
                continue

            print(f"Thought: {action.thought}")
            print(f"Executing action: {action.action_type}")

            result = self.execute_action(action)
            print(f"Result: {result}")

            if action.action_type.lower() == "task_complete":
                print(f"\n{'='*60}")
                print("Task completed!")
                print(f"{'='*60}")
                if self.task_summary:
                    print(f"\n📋 任务总结:\n{self.task_summary}")
                print(f"\n{'='*60}\n")
                return result

            time.sleep(self.delay)

        if self.max_iterations != -1:
            print("\nMax iterations reached without completing the task")
        else:
            print("\nTask interrupted without completing")
        return "Task incomplete - max iterations reached"


def main():
    agent = ScreenAgent()

    task = input("Enter your task: ")
    result = agent.run(task)
    print(f"\nFinal result: {result}")


if __name__ == "__main__":
    main()
