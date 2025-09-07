import sys
import os
import json
import time
import platform
import re
import ast
import xml.etree.ElementTree as ET
import inspect
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import requests
from typing import List, Dict
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QPushButton, QLabel, QSplitter,
                             QFileDialog, QMessageBox, QScrollArea, QFrame, QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor, QPalette, QColor


# 保留原有的 AppLogger 类
class AppLogger:
    def __init__(self):
        """Initialize the logger with a file that will be cleared on startup."""
        os.makedirs('./logs', exist_ok=True)
        self.log_file = f"./logs/log.log"
        # Clear the log file on startup
        with open(self.log_file, 'w', encoding="utf-8") as f:
            f.write("")

    def log(self, message):
        """Log a message to both file and console."""
        # Log to file
        with open(self.log_file, 'a', encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d-%H:%M:%S:\n", time.localtime()) + message + "\n")


logger = AppLogger()


# 工作线程类，用于在后台执行LLM请求
class LLMWorker(QThread):
    response_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    stream_chunk = pyqtSignal(str)
    usage_info = pyqtSignal(dict)

    def __init__(self, llm_chat, message):
        super().__init__()
        self.llm_chat = llm_chat
        self.message = message

    def run(self):
        try:
            # 使用带工具调用的对话
            response = self.llm_chat.conversation_with_tool(self.message, self.stream_chunk.emit, self.usage_info.emit)
            self.response_received.emit(response)
        except Exception as e:
            self.error_occurred.emit(str(e))


# 主窗口类
class ChatWindow(QMainWindow):
    def __init__(self, llm_chat):
        super().__init__()
        self.llm_chat = llm_chat
        self.setWindowTitle("LLM 聊天助手")
        self.setGeometry(100, 100, 1000, 700)

        # 设置中心窗口
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout(central_widget)

        # 创建分割器
        splitter = QSplitter(Qt.Orientation.Vertical)

        # 聊天显示区域
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setFont(QFont("Arial", 11))

        # 输入区域
        input_widget = QWidget()
        input_layout = QVBoxLayout(input_widget)

        # 按钮区域
        button_layout = QHBoxLayout()
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self.send_message)
        self.clear_button = QPushButton("清空对话")
        self.clear_button.clicked.connect(self.clear_chat)
        self.export_button = QPushButton("导出对话")
        self.export_button.clicked.connect(self.export_chat)

        button_layout.addWidget(self.send_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.export_button)

        # 输入框
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入您的问题...")
        self.input_field.returnPressed.connect(self.send_message)

        input_layout.addLayout(button_layout)
        input_layout.addWidget(self.input_field)

        # 添加到分割器
        splitter.addWidget(self.chat_display)
        splitter.addWidget(input_widget)

        # 设置分割器比例
        splitter.setSizes([500, 200])

        main_layout.addWidget(splitter)

        # 状态栏
        self.statusBar().showMessage("就绪")

        # 当前助手消息缓冲区
        self.current_assistant_message = ""

        # 当前工作线程
        self.current_worker = None

        # 初始化聊天显示
        self.update_chat_display()

    def send_message(self):
        message = self.input_field.text().strip()
        if not message:
            return

        # 添加到聊天记录
        self.llm_chat.history.append({
            "role": "user",
            "content": message
        })

        # 更新聊天显示
        self.update_chat_display()

        # 清空输入框
        self.input_field.clear()

        # 禁用发送按钮
        self.send_button.setEnabled(False)
        self.input_field.setEnabled(False)

        # 重置当前助手消息
        self.current_assistant_message = ""

        # 显示等待消息
        self.append_to_chat("assistant", "思考中...")

        # 创建工作线程处理LLM请求
        self.current_worker = LLMWorker(self.llm_chat, message)
        self.current_worker.stream_chunk.connect(self.handle_stream_chunk)
        self.current_worker.response_received.connect(self.handle_response)
        self.current_worker.error_occurred.connect(self.handle_error)
        self.current_worker.usage_info.connect(self.handle_usage_info)
        self.current_worker.start()

    def handle_stream_chunk(self, chunk):
        # 删除"思考中..."消息
        if self.current_assistant_message == "":
            cursor = self.chat_display.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()

        # 添加到当前消息缓冲区
        self.current_assistant_message += chunk

        # 更新显示
        self.update_chat_display()

    def handle_usage_info(self, usage):
        usage_msg = (
            f"\n本次请求用量："
            f"提示 {usage['prompt_tokens']} tokens，"
            f"生成 {usage['completion_tokens']} tokens，"
            f"总计 {usage['total_tokens']} tokens。"
        )
        self.statusBar().showMessage(usage_msg)
        logger.log(usage_msg)

    def handle_response(self, response):
        # 将最终响应添加到历史记录
        self.llm_chat.history.append({
            "role": "assistant",
            "content": response
        })

        # 启用发送按钮和输入框
        self.send_button.setEnabled(True)
        self.input_field.setEnabled(True)

        # 更新状态栏
        self.statusBar().showMessage("响应接收完成")

        # 清空当前助手消息
        self.current_assistant_message = ""

        # 更新显示
        self.update_chat_display()

        # 检查是否完成
        if "<attempt_completion>" in response:
            logger.log("检测到 <attempt_completion>，会话结束")
            QMessageBox.information(self, "完成", "AI 已标记任务完成。")

    def handle_error(self, error_message):
        # 显示错误信息
        self.append_to_chat("system", f"错误: {error_message}")

        # 启用发送按钮和输入框
        self.send_button.setEnabled(True)
        self.input_field.setEnabled(True)

        # 更新状态栏
        self.statusBar().showMessage(f"错误: {error_message}")

        # 清空当前助手消息
        self.current_assistant_message = ""

    def append_to_chat(self, role, message):
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # 设置不同角色的文本格式
        if role == "user":
            prefix = "用户: "
            color = QColor(0, 0, 255)  # 蓝色
        elif role == "assistant":
            prefix = "助手: "
            color = QColor(0, 100, 0)  # 深绿色
        else:
            prefix = "系统: "
            color = QColor(255, 0, 0)  # 红色

        # 插入文本
        text_format = cursor.charFormat()
        text_format.setForeground(color)
        cursor.setCharFormat(text_format)
        cursor.insertText(prefix)

        # 重置格式
        text_format.setForeground(QColor(0, 0, 0))  # 黑色
        cursor.setCharFormat(text_format)
        cursor.insertText(message + "\n\n")

        # 滚动到底部
        self.chat_display.ensureCursorVisible()

    def update_chat_display(self):
        self.chat_display.clear()

        # 只显示用户和助手消息，不显示系统消息
        for msg in self.llm_chat.history:
            if msg["role"] == "user":
                self.append_to_chat("user", msg["content"])
            elif msg["role"] == "assistant":
                self.append_to_chat("assistant", msg["content"])

        # 显示当前正在生成的助手消息
        if self.current_assistant_message:
            self.append_to_chat("assistant", self.current_assistant_message)

    def clear_chat(self):
        # 保留系统提示
        system_prompt = self.llm_chat.history[0] if self.llm_chat.history else None
        self.llm_chat.history = []
        if system_prompt:
            self.llm_chat.history.append(system_prompt)

        # 更新显示
        self.update_chat_display()
        self.statusBar().showMessage("对话已清空")

    def export_chat(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出对话", "", "Text Files (*.txt);;All Files (*)")

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    for msg in self.llm_chat.history:
                        if msg["role"] == "user":
                            f.write(f"用户: {msg['content']}\n\n")
                        elif msg["role"] == "assistant":
                            f.write(f"助手: {msg['content']}\n\n")
                self.statusBar().showMessage(f"对话已导出到: {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出失败: {str(e)}")


# 修改后的主函数
def main():
    # 创建应用实例
    app = QApplication(sys.argv)

    # 初始化LLM聊天
    try:
        llm_chat = LLMChat(os.getcwd())
    except Exception as e:
        QMessageBox.critical(None, "初始化错误", f"无法初始化LLM聊天: {str(e)}")
        return

    # 创建主窗口
    window = ChatWindow(llm_chat)
    window.show()

    # 运行应用
    sys.exit(app.exec())


# 重构后的 LLMChat 类
class LLMChat:
    def __init__(self, conversations_folder):
        self.history = []
        self.api_key = self.get_api_key()
        self.model = self.get_model_name()
        self.base_url = "https://api-inference.modelscope.cn/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.os_name = platform.system()
        self.conversations_folder = conversations_folder
        if self.os_name == "Windows":
            self.os_main_folder = os.getenv("USERPROFILE")
        if self.os_name == "Linux":
            self.os_main_folder = os.path.expanduser("~")
        if self.os_name == "Darwin":
            self.os_main_folder = os.getenv("HOME")
        self.get_prompt()
        if not self.Connectivity():
            raise ConnectionError
        else:
            logger.log("成功连接")
        self.history.append({
            "role": "system",
            "content": f"{self.prompt}"
        })
        self.agent = Agent(self)

    def get_api_key(self) -> str:
        """Load the API key from an environment variable."""
        load_dotenv()
        api_key = os.getenv("MODELSCOPE_API_KEY")
        if not api_key:
            raise ValueError("未找到 MODELSCOPE_API_KEY 环境变量，请在 .env 文件中设置。")
        return api_key

    def get_model_name(self) -> str:
        """Get the model name from the environment variable."""
        load_dotenv()
        model_name = os.getenv("MODEL_NAME")
        if not model_name:
            model_name = "deepseek-ai/DeepSeek-V3.1"  # 默认模型
        return model_name

    def Connectivity(self):
        test_history = [
            {
                "role": "system",
                "content": "测试连接"
            },
            {
                "role": "user",
                "content": "你好"
            }
        ]

        request_body = {
            "model": self.model,
            "messages": test_history,
            "stream": False
        }

        response = requests.post(
            self.base_url,
            headers=self.headers,
            json=request_body
        )

        if response.status_code != 200:
            logger.log(f"API request failed with status {response.status_code}: {response.text}")
            return False

        return True

    def get_prompt(self):
        with open("prompt.md", "r", encoding="utf-8") as f:
            self.prompt = f.read()
        self.prompt = self.prompt.format(
            conversations_folder=self.conversations_folder,
            osname=self.os_name,
            os_main_folder=self.os_main_folder,
            mcp_server="没有连接任何mcp工具"
        )
        logger.log("系统提示词:\n" + self.prompt)

    def conversation_with_tool(self, messages=None, stream_callback=None, usage_callback=None) -> str:
        if not hasattr(self, 'agent'):
            self.agent = Agent(self)

        if messages:
            self.history.append({"role": "user", "content": messages})

        request_body = {
            "model": self.model,
            "messages": self.history,
            "stream": True,
            "stream_options": {"include_usage": True}
        }

        response = requests.post(
            self.base_url,
            headers={
                **self.headers,
                "Accept-Charset": "utf-8",
                "Accept": "text/event-stream"
            },
            json=request_body,
            stream=True
        )
        response.encoding = 'utf-8'

        full_content = ""
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            line = line.strip()
            if not line.startswith('data: '):
                continue

            data = line[6:]
            if data == '[DONE]':
                break

            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                logger.log(f"非法 JSON：{data}")
                continue

            choices = chunk.get('choices')
            if choices and len(choices) > 0:
                delta = choices[0].get('delta') or {}
                content = delta.get('content', '')
                if content:
                    full_content += content
                    if stream_callback:
                        stream_callback(content)

            if 'usage' in chunk and usage_callback:
                usage_callback(chunk['usage'])

        # ---------- 处理工具调用 ----------
        clean_pattern = re.compile(r'</?(out_text|thinking)>', flags=re.S)
        xml_pattern = re.compile(r'<(\w+)>.*?</\1>', flags=re.S)

        clean_content = clean_pattern.sub('', full_content)
        logger.log(clean_content)
        xml_blocks = [m.group(0) for m in xml_pattern.finditer(clean_content)]

        # 一次性执行所有工具，并把结果汇总
        tool_results = []
        for block in xml_blocks:
            try:
                logger.log(f"[DEBUG] 执行 XML: {repr(block)}")
                result = str(self.agent.run(block))
                tool_results.append(result)
            except Exception as e:
                tool_results.append(f"工具异常：{e}")

        if tool_results:
            # 把所有工具结果一次性给 AI，让它继续思考
            self.history.append({"role": "system",
                                 "content": "工具返回：\n" + "\n".join(tool_results)})
            full_content = self.conversation(stream_callback, usage_callback)

        return full_content

    def conversation(self, stream_callback=None, usage_callback=None):
        request_body = {
            "model": self.model,
            "messages": self.history,
            "stream": True,
            "stream_options": {"include_usage": True}
        }

        response = requests.post(
            self.base_url,
            headers={
                **self.headers,
                "Accept-Charset": "utf-8",
                "Accept": "text/event-stream"
            },
            json=request_body,
            stream=True
        )
        response.encoding = 'utf-8'

        full_content = ""
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            line = line.strip()
            if not line.startswith('data: '):
                continue

            data = line[6:]
            if data == '[DONE]':
                break

            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                logger.log(f"非法 JSON：{data}")
                continue

            choices = chunk.get('choices')
            if choices and len(choices) > 0:
                delta = choices[0].get('delta') or {}
                content = delta.get('content', '')
                if content:
                    full_content += content
                    if stream_callback:
                        stream_callback(content)

            if 'usage' in chunk and usage_callback:
                usage_callback(chunk['usage'])

        return full_content


# 保留原有的 Agent 类
class Agent:
    """XML 指令自动调用工具"""

    def __init__(self, llm: "LLMChat"):
        self.llm = llm
        self._tools = {
            name: getattr(self, name)
            for name in dir(self)
            if not name.startswith('_') and callable(getattr(self, name))
        }

    # ============================tools============================
    def execute_command(self,
                        command: str,
                        requires_approval: bool,
                        ) -> str:
        """
        在指定目录下执行命令，跨平台兼容。

        :param command:          要执行的 CLI 命令（字符串）。
        :param requires_approval: 布尔值，指示该命令是否需要用户批准才能执行。
        :param target_dir:        执行命令前切换的工作目录。
        :return:                  命令的标准输出与标准错误（合并后字符串）。
        """
        target_dir = self.llm.conversations_folder
        if not os.path.isdir(target_dir):
            raise NotADirectoryError(f"目录不存在: {target_dir}")

        # 根据平台构造 shell 调用
        if platform.system() == "Windows":
            cmd = ["cmd", "/c", command]
        else:
            cmd = ["/bin/sh", "-c", command]

        # 需要审批时，向用户确认
        if requires_approval:
            ans = input(f"需要执行命令: {command}\n在目录: {target_dir}\n是否继续? [y/N] ").strip().lower()
            if ans != "y":
                return "用户取消执行"

        proc = subprocess.run(
            cmd,
            cwd=target_dir,
            capture_output=True,
            text=True
        )
        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            raise RuntimeError(f"命令返回非零状态({proc.returncode})\n{output}")
        return output

    def read_file(self, path: str) -> str:
        logger.log(f"[DEBUG] read_file called with path={path}")
        file_suffix = "." + path.split(".")[-1]
        special_suffix = [
            '.asm', '.bash', '.bat', '.bib', '.c', '.cc', '.cfg', '.cls', '.cmd',
            '.conf', '.config', '.cpp', '.css', '.csv', '.cxx', '.diff', '.dockerfile',
            '.env', '.f', '.f03', '.f08', '.f90', '.f95', '.fish', '.for', '.go',
            '.h', '.hh', '.hlsl', '.hpp', '.htm', '.html', '.hxx', '.ini', '.java',
            '.jenkinsfile', '.js', '.json', '.jsx', '.jsp', '.kt', '.latex', '.less',
            '.log', '.lua', '.m', '.markdown', '.md', '.mm', '.mjs', '.pas', '.patch',
            '.php', '.pl', '.pm', '.properties', '.ps1', '.py', '.pyx', '.r', '.rb',
            '.rs', '.rst', '.s', '.sass', '.scala', '.scss', '.sh', '.sql', '.sty',
            '.sv', '.svh', '.swift', '.tab', '.tex', '.toml', '.ts', '.tsx', '.tsv',
            '.txt', '.v', '.vb', '.vhd', '.vhdl', '.xhtml', '.xml', '.yaml', '.yml',
            '.zsh'
        ]
        if file_suffix not in special_suffix:
            return "暂时无法为您处理文件"
        else:
            try:
                file_path = Path(self.llm.conversations_folder) / path
                logger.log(f"[DEBUG] full path={file_path.resolve()}")
                with open(file_path.resolve(), "r", encoding="utf-8") as f:
                    content = f.read()
                logger.log(f"[DEBUG] read_file success, len={len(content)}")
                return content
            except Exception as e:
                logger.log(f"[DEBUG] read_file exception: {e}")
                return f"读取失败: {e}"

    def write_to_file(self, path: str, content: str):
        logger.log(f"[write_to_file] 准备写入: {Path(self.llm.conversations_folder) / path}")
        file_suffix = "." + path.split(".")[-1]
        special_suffix = [
            '.asm', '.bash', '.bat', '.bib', '.c', '.cc', '.cfg', '.cls', '.cmd',
            '.conf', '.config', '.cpp', '.css', '.csv', '.cxx', '.diff', '.dockerfile',
            '.env', '.f', '.f03', '.f08', '.f90', '.f95', '.fish', '.for', '.go',
            '.h', '.hh', '.hlsl', '.hpp', '.htm', '.html', '.hxx', '.ini', '.java',
            '.jenkinsfile', '.js', '.json', '.jsx', '.jsp', '.kt', '.latex', '.less',
            '.log', '.lua', '.m', '.markdown', '.md', '.mm', '.mjs', '.pas', '.patch',
            '.php', '.pl', '.pm', '.properties', '.ps1', '.py', '.pyx', '.r', '.rb',
            '.rs', '.rst', '.s', '.sass', '.scala', '.scss', '.sh', '.sql', '.sty',
            '.sv', '.svh', '.swift', '.tab', '.tex', '.toml', '.ts', '.tsx', '.tsv',
            '.txt', '.v', '.vb', '.vhd', '.vhdl', '.xhtml', '.xml', '.yaml', '.yml',
            '.zsh'
        ]
        if file_suffix not in special_suffix:
            return "暂时无法为您处理文件"
        else:
            try:
                with open((Path(self.llm.conversations_folder) / path).resolve(), "w+", encoding="utf-8") as f:
                    f.write(content)
                return "文件写入成功"
            except Exception as e:
                return f"文件写入失败: {e}"

    def replace_in_file(self, path: str, diff: str):
        """
        模拟 replace_in_file 的行为，根据 diff 中的 SEARCH/REPLACE 块修改文件。
        """
        # 读取原文件内容
        full_path = (Path(self.llm.conversations_folder) / path).resolve()
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            return f"文件读取失败: {e}"

        # 解析 diff 中的 SEARCH/REPLACE 块
        pattern = re.compile(
            r'<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE',
            re.DOTALL
        )

        # 替换每个匹配到的 SEARCH 块
        matches = pattern.findall(diff)
        for search, replace in matches:
            # 精确匹配（包括换行和缩进）
            if search in content:
                content = content.replace(search, replace, 1)
            else:
                return "SEARCH 内容未找到，无法替换"

        # 写回文件
        try:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return "文件替换成功"
        except Exception as e:
            return f"文件写入失败: {e}"

    def list_files(self, path: str, recursive: bool = False) -> List[str]:
        """
        列出目录内容。
        :param path: 相对路径
        :param recursive: 是否递归
        :return: 相对路径字符串列表（文件/目录）
        """
        root = Path(self.llm.conversations_folder) / path
        if not root.exists():
            return [f"目录不存在: {path}"]

        if recursive:
            return [str(p.relative_to(self.llm.conversations_folder)) for p in root.rglob('*')]
        else:
            return [str(p.relative_to(self.llm.conversations_folder)) for p in root.iterdir()]

    def _scan_file(self, file: Path) -> Dict[str, List[str]]:
        """解析单个 Python 文件，返回顶层定义名称"""
        try:
            tree = ast.parse(file.read_text(encoding='utf-8'), filename=str(file))
        except Exception:
            return {}

        defs = {"classes": [], "functions": [], "methods": []}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                defs["classes"].append(node.name)
            elif isinstance(node, ast.FunctionDef):
                defs["functions"].append(node.name)
        return defs

    def list_code_definition_names(self, path: str) -> Dict[str, Dict[str, List[str]]]:
        """
        扫描目录顶层 Python 文件，返回各文件的定义名称。
        :param path: 目录相对路径
        :return: {文件相对路径: {"classes": [..], "functions": [..]}}
        """
        root = Path(self.llm.conversations_folder) / path
        if not root.exists():
            return {f"错误: 目录不存在: {path}": {"classes": [], "functions": []}}

        result = {}
        for py_file in root.glob("*.py"):
            result[str(py_file.relative_to(self.llm.conversations_folder))] = self._scan_file(py_file)
        return result

    def search_files(self, path: str, regex: str, file_pattern: str = None) -> List[Dict]:
        """
        递归搜索目录，返回匹配行及其上下文。
        :param path: 目录相对路径
        :param regex: 正则表达式字符串
        :param file_pattern: glob 过滤，如 "*.ts"
        :return: 列表，每项为 {"file": ..., "line": ..., "context": ...}
        """
        try:
            pattern = re.compile(regex)
        except re.error:
            return [{"error": f"无效的正则表达式: {regex}"}]

        root = Path(self.llm.conversations_folder) / path
        if not root.exists():
            return [{"error": f"目录不存在: {path}"}]

        matches = []

        def _iter_files():
            if file_pattern:
                return root.rglob(file_pattern)
            else:
                return (p for p in root.rglob('*') if p.is_file())

        for file in _iter_files():
            try:
                lines = file.read_text(encoding='utf-8').splitlines()
            except UnicodeDecodeError:
                continue  # 跳过二进制文件

            for idx, line in enumerate(lines, 1):
                if pattern.search(line):
                    context = "\n".join(
                        lines[max(0, idx - 2): idx + 1]
                    ).strip()
                    matches.append({
                        "file": str(file.relative_to(self.llm.conversations_folder)),
                        "line": idx,
                        "context": context
                    })
        return matches

    def attempt_completion(self, result, command=None):
        if command:
            return self.execute_command(command, False)
        return result

    # ===== 调度 =====
    def run(self, xml_cmd: str):
        """
        解析 XML 指令 -> 调用对应工具。
        支持：
            <read_file>
                <path>tools.py</path>
            </read_file>
        其中节点文本如果是 list/dict/数字会安全反序列化，
        否则按字符串处理。
        """

        def safe_eval(text: str):
            """把节点文本安全地反序列化成 Python 对象。"""
            text = text.strip()
            try:
                # 尝试解析为字面量（list / dict / int / float / bool / None）
                return ast.literal_eval(text)
            except (ValueError, SyntaxError):
                # 失败就原样返回字符串
                return text

        try:
            root = ET.fromstring(xml_cmd.strip())
        except ET.ParseError:
            return f"XML 解析错误: {xml_cmd}"

        tool_name = root.tag

        if tool_name not in self._tools:
            return f"未知工具：{tool_name}"

        # 把子节点文本转换成参数字典
        kwargs = {}
        for child in root:
            kwargs[child.tag] = safe_eval(child.text) if child.text else ""

        # 校验参数与函数签名
        try:
            sig = inspect.signature(self._tools[tool_name])
            sig.bind(**kwargs)
        except TypeError as e:
            return f"参数错误: {e}"

        # 真正调用
        try:
            return self._tools[tool_name](**kwargs)
        except Exception as e:
            return f"工具执行错误: {e}"


if __name__ == "__main__":
    main()