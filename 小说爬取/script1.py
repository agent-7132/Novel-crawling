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
        'Accept': 'text/html,application/xhtml+xml;q=0.9,image/webp,*/*;q=0.8',
        'Referer': 'http://m.ggdwx.net/',
        'Accept-Encoding': 'gzip, deflate'
    },
    'save_path': '/storage/emulated/0/Download/novels',
    'request_interval': 3,
    'max_retries': 5,
    'termux_notify': True
}

class GgdwxDownloader:
    def __init__(self):
        self.start_url = None
        self.current_url = None
        self.session = requests.Session()
        self.session.headers.update(config['headers'])
        self.chapter_count = 0
        self.novel_name = None
        self.merge_action = 0
        os.makedirs(config['save_path'], exist_ok=True)
        self.is_termux = 'com.termux' in os.getcwd()
        self.js_next_page = None

    def show_notification(self, title, message):
        if self.is_termux and config['termux_notify']:
            try:
                subprocess.run([
                    'termux-notification',
                    '--title', title,
                    '--content', message,
                    '--led-color', 'FF00FF00'
                ], check=True)
            except Exception as e:
                print(f"通知发送失败: {str(e)}")

    def termux_dialog(self, dialog_type, title, values=None, default_input=""):
        try:
            cmd = ['termux-dialog']
            if dialog_type == "text":
                cmd += ['text', '-t', title, '-i', default_input]
            elif dialog_type == "radio":
                cmd += ['radio', '-v', ','.join(values), '-t', title]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            return json.loads(result.stdout.strip()) if result.stdout else None
        except Exception as e:
            print(f"对话框异常: {str(e)}")
            return None

    def get_user_input(self):
        url_dialog = self.termux_dialog(
            "text", 
            "📖 请输入小说起始网址",
            default_input="http://m.ggdwx.net/book/120386/53805857.html"
        )
        if url_dialog and url_dialog.get('text'):
            self.start_url = url_dialog['text'].strip()
            if not re.match(r'^https?://m\.ggdwx\.net/book/\d+/\d+\.html$', self.start_url):
                self.show_notification("URL错误", "无效的章节URL格式")
                return False
            return True
        return False

    def sanitize_filename(self, name):
        return re.sub(r'[\\/:*?"<>|]', '', name).strip()[:80]

    def extract_novel_name(self, title_text):
        title_parts = title_text.split('_')
        if len(title_parts) > 1:
            return self.sanitize_filename(title_parts[0])
        return self.sanitize_filename(title_text.split('最新')[0])

    def get_page_content(self, url):
        for retry in range(config['max_retries']):
            try:
                response = self.session.get(url, timeout=15)
                response.encoding = 'utf-8'
                if response.status_code == 404:
                    self.show_notification("章节不存在", f"URL: {url}")
                    return None
                response.raise_for_status()
                return response.text
            except Exception as e:
                print(f"请求失败({retry+1}/{config['max_retries']}): {str(e)}")
                time.sleep(2)
        return None

    def clean_content(self, text):
        text = unescape(text)
        patterns = {
            r'&nbsp;|\u3000': ' ',
            r'[\s\n]*「如章节缺失请退#出#阅#读#模#式」[\s\n]*': '',
            r'[\s\n]*防采集.*?格格党.*?[\s\n]*': '',
            r'内容未完[^\n]+': '',
            r'\n{3,}': '\n\n'
        }
        for pattern, repl in patterns.items():
            text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
        return text.strip()

    def parse_page(self, html, current_url):
        soup = BeautifulSoup(html, 'lxml')
        
        # 提取标题
        title_tag = soup.find('title')
        title_text = title_tag.get_text().strip() if title_tag else "未知章节"
        title_parts = [p.strip() for p in title_text.split('_') if p.strip()]
        
        if not self.novel_name and len(title_parts) > 1:
            self.novel_name = self.sanitize_filename(title_parts[0])
            self.show_notification("开始下载", f"《{self.novel_name}》")
        
        # 提取章节标题
        chapter_title = title_parts[0] if len(title_parts) == 1 else title_parts[1]
        chapter_title = re.sub(r'^.*?(第[^章]+章)', r'\1', chapter_title)
        
        # 提取内容
        content_div = soup.find('div', {'id': 'txt'})
        if not content_div:
            raise ValueError("未找到章节内容")
        
        # 处理动态排序的dd元素
        dd_list = content_div.find_all('dd', attrs={'data-id': True})
        sorted_dd = sorted(dd_list, key=lambda x: int(x['data-id']))
        
        # 合并内容段落
        content_parts = []
        for dd in sorted_dd:
            paragraphs = dd.find_all('p')
            content_parts.extend([p.get_text(strip=True) for p in paragraphs] if paragraphs 
                                else [dd.get_text(strip=True)])
        
        # 提取下一页链接
        next_url = None
        script_text = soup.find('script', string=re.compile('var next_page'))
        if script_text:
            match = re.search(r'var\s+next_page\s*=\s*["\'](.*?)["\'];', script_text.string)
            if match:
                next_url = urljoin(current_url, match.group(1))
        
        if not next_url:
            next_span = soup.find('span', class_=re.compile('c67da7064a45a9'))
            if next_span:
                next_a = next_span.find('a')
                if next_a and next_a.get('href'):
                    next_url = urljoin(current_url, next_a['href'])
        
        return chapter_title, '\n\n'.join(content_parts), next_url

    def download_chapters(self):
        current_url = self.start_url
        novel_dir = os.path.join(config['save_path'], self.novel_name)
        os.makedirs(novel_dir, exist_ok=True)
        
        while current_url:
            html = self.get_page_content(current_url)
            if not html:
                break
            
            try:
                title, content, next_url = self.parse_page(html, current_url)
            except Exception as e:
                self.show_notification("解析错误", f"{str(e)}")
                break
            
            # 保存章节
            filename = f"{self.chapter_count:04d}_{title}.txt"
            filepath = os.path.join(novel_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"{title}\n\n{self.clean_content(content)}")
            
            self.chapter_count += 1
            self.show_notification("下载进度", 
                f"{self.novel_name}\n已下载 {self.chapter_count} 章")
            
            current_url = next_url if next_url and next_url != current_url else None
            time.sleep(config['request_interval'])
        
        # 合并章节
        if self.merge_action in [0, 2]:
            self.merge_chapters(novel_dir)
            if self.merge_action == 0:
                for f in os.listdir(novel_dir):
                    if f.endswith('.txt') and not f.startswith('merged_'):
                        os.remove(os.path.join(novel_dir, f))

    def merge_chapters(self, novel_dir):
        merged_file = os.path.join(novel_dir, f"merged_{self.novel_name}.txt")
        with open(merged_file, 'w', encoding='utf-8') as outfile:
            for fname in sorted(os.listdir(novel_dir)):
                if fname.endswith('.txt') and not fname.startswith('merged_'):
                    with open(os.path.join(novel_dir, fname), 'r', encoding='utf-8') as infile:
                        outfile.write(infile.read() + '\n\n')
        self.show_notification("合并完成", f"《{self.novel_name}》已合并")

    def run(self):
        if self.get_user_input():
            self.download_chapters()

if __name__ == "__main__":
    downloader = GgdwxDownloader()
    downloader.run()
