import requests
from bs4 import BeautifulSoup
import time
import os
import re
import subprocess
import json
import uuid
from urllib.parse import urljoin
from html import unescape

config = {
    'headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml;q=0.9,image/webp,*/*;q=0.8'
    },
    'save_path': '/storage/emulated/0/Download/novels',
    'request_interval': 2,
    'max_retries': 5,
    'termux_notify': True
}

class BiqugeDownloader:
    def __init__(self):
        self.start_url = None
        self.current_url = None
        self.session = requests.Session()
        self.session.headers.update(config['headers'])
        self.chapter_count = 0
        self.novel_name = None
        self.merge_action = 0  # 0:合并删除 1:仅保存 2:合并保留
        os.makedirs(config['save_path'], exist_ok=True)
        self.is_termux = 'com.termux' in os.getcwd()

    def show_notification(self, title, message):
        if self.is_termux and config['termux_notify']:
            try:
                subprocess.run([
                    'termux-notification',
                    '--title', title,
                    '--content', message,
                    '--led-color', 'FF00FF00'
                ], check=True)
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                print(f"通知发送失败: {str(e)}")

    def termux_dialog(self, dialog_type, title, values=None, default_input=""):
        try:
            cmd = ['termux-dialog']
            if dialog_type == "text":
                cmd += ['text', '-t', title, '-i', default_input]
            elif dialog_type == "radio":
                cmd += ['radio', '-v', ','.join(values), '-t', title]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=45
            )
            raw_output = result.stdout.strip()
           # print(f"[DEBUG] Dialog Raw Output: {raw_output}")

            # 增强JSON解析
            data = {}
            try:
                data = json.loads(raw_output)
            except json.JSONDecodeError:
                code_match = re.search(r'"code"\s*:\s*(-?\d+)', raw_output)
                text_match = re.search(r'"text"\s*:\s*"?(.*?)"?[\},]', raw_output)
                data = {
                    "code": int(code_match.group(1)) if code_match else -2,
                    "text": text_match.group(1).strip() if text_match else ""
                }

           # print(f"[DEBUG] Parsed Data: {data}")
            return data if data.get('text') else None

        except Exception as e:
            print(f"对话框异常: {str(e)}")
            return None

    def get_user_input(self):
        # 获取起始URL
        url_dialog = self.termux_dialog(
            "text", 
            "📖 请输入小说起始网址",
            default_input="https://www.biquge.com/book/"
        )
        
        if url_dialog and url_dialog.get('text', '').strip():
            self.start_url = self.current_url = url_dialog['text'].strip()
            #print(f"获取到有效URL: {self.start_url}")
            time.sleep(0.5)
        else:
            self.show_notification("输入取消", "未提供起始网址")
            return False
        
        # 获取操作选项
        action_options = [
            "🚀 合并后删除章节", 
            "💾 仅保存章节文件", 
            "📚 合并保留章节"
        ]
        action_dialog = self.termux_dialog(
            "radio",
            "🛠️ 请选择操作模式",
            values=action_options
        )
        
        # 处理操作选择
        if action_dialog and action_dialog.get('text'):
            try:
                selected_index = int(action_dialog['text'])
                if 0 <= selected_index < len(action_options):
                    self.merge_action = selected_index
                else:
                    raise ValueError("无效的选项索引")
            except Exception as e:
                print(f"选项解析错误: {str(e)}，使用默认模式")
                self.merge_action = 0
        else:
            print("使用默认合并模式")
            self.merge_action = 0
        
        return True

    def sanitize_filename(self, name):
        return re.sub(r'[\\/:*?"<>|]', '', name).strip()[:80]

    def extract_novel_name(self, title_text):
        patterns = [
            r'(.*?)最新章节', 
            r'(.*?)\s*-\s*',
            r'最新章：(.*?)\s*\|'
        ]
        for pattern in patterns:
            match = re.search(pattern, title_text)
            if match:
                return self.sanitize_filename(match.group(1))
        parts = [p.strip() for p in title_text.split('-')]
        return self.sanitize_filename(parts[0]) if len(parts) > 1 else f"无名小说_{uuid.uuid4().hex[:6]}"

    def get_page_content(self, url):
        for retry in range(config['max_retries'])exit:
            try:
                response = self.session.get(url, timeout=15)
                response.encoding = 'utf-8'
                response.raise_for_status()
                
                if not self.novel_name:
                    soup = BeautifulSoup(response.text, 'lxml')
                    title_tag = soup.find('h1') or soup.find('title')
                    if title_tag:
                        self.novel_name = self.extract_novel_name(title_tag.get_text().strip())
                        self.show_notification("开始下载", f"《{self.novel_name}》")
                
                return response.text
            except Exception as e:
                print(f"请求失败({retry+1}/{config['max_retries']}): {str(e)}")
                time.sleep(2)
        return None

    def clean_content(self, text):
        text = unescape(text)
        replacements = {
            r'&nbsp;|\u3000': ' ',
            r'内容未完[^\n]+': '',
            r'还不赶快来体验！+': '',
            r'请收藏本站：https://www\.biquge\d+\.com。\s*笔趣阁手机版：https://m\.biquge\d+\.com': ''
        }
        for pattern, repl in replacements.items():
            text = re.sub(pattern, repl, text)
        return '\n\n'.join([line.strip() for line in text.split('\n') if line.strip()])

    def parse_page(self, html):
        soup = BeautifulSoup(html, 'lxml')
        
        # 提取标题
        title_tag = soup.find('h1') or soup.find('title')
        title_text = title_tag.get_text().strip() if title_tag else "未知章节"
        chapter_title = re.sub(r'^.*?(第[^章]+章)', r'\1', title_text.split('-')[0].strip())
        
        # 提取内容
        content_div = soup.find('div', id='novelcontent')
        if not content_div:
            raise ValueError("未找到章节内容")
        
        # 清理广告
        for tag in content_div.find_all(['script', 'font', 'div', 'a']):
            tag.decompose()
        
        # 处理分页
        next_url = None
        page_nav = soup.find('div', class_='page_chapter')
        if page_nav:
            next_btn = page_nav.find('a', string=re.compile(r'下一頁|下一页|下一章'))
            if next_btn and next_btn.get('href'):
                next_url = urljoin(self.current_url, next_btn['href'])
        
        return {
            'title': self.sanitize_filename(chapter_title),
            'content': self.clean_content(content_div.get_text(separator='\n')),
            'next_url': next_url if next_url != self.current_url else None
        }

    def save_chapter(self, title, content):
        self.chapter_count += 1
        filename = os.path.join(config['save_path'], f"{self.chapter_count:04d}_{title}.txt")
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"【{title}】\n\n{content}\n")
            print(f"✅ 已保存: {filename}")
            return True
        except PermissionError:
            print("❌ 权限不足，请执行：termux-setup-storage")
            self.show_notification("错误", "需要存储权限")
            exit(1)
        except Exception as e:
            print(f"❌ 保存失败: {str(e)}")
            return False

    def merge_chapters(self):
        chapter_files = sorted(
            [f for f in os.listdir(config['save_path']) 
             if re.match(r'^\d{4}_', f) and f.endswith('.txt')],
            key=lambda x: int(x.split('_')[0])
        )
        
        if not chapter_files:
            print("⚠️ 没有可合并的章节")
            return None
        
        merged_name = f"{self.novel_name}.txt" if self.novel_name else f"合并小说_{time.strftime('%Y%m%d%H%M')}.txt"
        merged_path = os.path.join(config['save_path'], merged_name)
        
        try:
            with open(merged_path, 'w', encoding='utf-8') as mf:
                total = len(chapter_files)
                for idx, cf in enumerate(chapter_files, 1):
                    cf_path = os.path.join(config['save_path'], cf)
                    with open(cf_path, 'r', encoding='utf-8') as sf:
                        mf.write(sf.read() + '\n\n')
                    print(f"📦 合并进度: {idx}/{total}")
            
            print(f"✅ 合并完成: {merged_path}")
            self.show_notification("合并成功", os.path.basename(merged_path))
            return merged_path
        except Exception as e:
            print(f"❌ 合并失败: {str(e)}")
            self.show_notification("合并失败", str(e))
            return None

    def merge_and_clean(self):
        if merged_file := self.merge_chapters():
            chapter_files = [f for f in os.listdir(config['save_path']) 
                           if re.match(r'^\d{4}_', f) and f.endswith('.txt')]
            for cf in chapter_files:
                try:
                    os.remove(os.path.join(config['save_path'], cf))
                except Exception as e:
                    print(f"❌ 删除失败: {cf} - {str(e)}")
            print(f"🗑️ 已清理 {len(chapter_files)} 个章节文件")

    def download_all(self):
        if not self.get_user_input():
            return

        print("🏁 开始下载，按Ctrl+C停止")
        start_time = time.time()
        try:
            while self.current_url:
                print(f"\n📡 抓取: {self.current_url}")
                html = self.get_page_content(self.current_url)
                if not html:
                    print("🚨 获取页面失败")
                    break
                
                try:
                    data = self.parse_page(html)
                    self.save_chapter(data['title'], data['content'])
                    self.current_url = data['next_url']
                except Exception as e:
                    print(f"❌ 解析错误: {str(e)}")
                    break
                
                time.sleep(config['request_interval'])
            
            # 执行合并操作
            if self.merge_action == 0:
                self.merge_and_clean()
            elif self.merge_action == 2:
                self.merge_chapters()
            
            time_cost = time.time() - start_time
            stats = f"耗时: {time_cost:.1f}秒\n章节: {self.chapter_count}章"
            self.show_notification("下载完成", stats)
            
        except KeyboardInterrupt:
            print("\n🛑 用户中断")
            self.show_notification("下载中断", f"已保存 {self.chapter_count} 章")

if __name__ == "__main__":
    downloader = BiqugeDownloader()
    downloader.download_all()
