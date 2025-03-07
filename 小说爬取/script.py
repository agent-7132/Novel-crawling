import requests
from bs4 import BeautifulSoup
import time
import os
import re
import subprocess
import argparse
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
    def __init__(self, start_url=None):
        self.start_url = start_url
        self.current_url = start_url
        self.session = requests.Session()
        self.session.headers.update(config['headers'])
        self.chapter_count = 0
        self.novel_name = None  # 新增小说名称存储
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
                ])
            except FileNotFoundError:
                print("未找到termux-notification，请先安装termux-api")

    def sanitize_filename(self, name):
        return re.sub(r'[\\/:*?"<>|]', '', name).strip()[:80]

    def extract_novel_name(self, title_text):
        """从网页标题中提取小说名称"""
        patterns = [
            r'(.*?)最新章节',      # 匹配 "小说名最新章节"
            r'(.*?)\s*-\s*',      # 匹配 "小说名 - 网站名"
            r'最新章：(.*?)\s*\|' # 匹配 "最新章：小说名 |"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, title_text)
            if match:
                return self.sanitize_filename(match.group(1))
        
        # 如果所有模式都不匹配，尝试分割短横线
        parts = [p.strip() for p in title_text.split('-')]
        if len(parts) > 1:
            return self.sanitize_filename(parts[0])
        
        return f"无名小说_{uuid.uuid4().hex[:6]}"  # 最终回退方案

    def get_page_content(self, url):
        for retry in range(config['max_retries']):
            try:
                response = self.session.get(url, timeout=15)
                response.encoding = 'utf-8'
                response.raise_for_status()
                
                # 首次请求时解析小说名称
                if not self.novel_name and retry == 0:
                    soup = BeautifulSoup(response.text, 'lxml')
                    title_tag = soup.find('h1') or soup.find('title')
                    if title_tag:
                        title_text = title_tag.get_text().strip()
                        self.novel_name = self.extract_novel_name(title_text)
                
                return response.text
            except Exception as e:
                print(f"请求失败({retry+1}/{config['max_retries']}): {str(e)}")
                time.sleep(2)
        return None

    def clean_content(self, text):
        text = unescape(text)
        text = re.sub(r'&nbsp;|\u3000', ' ', text)
        text = re.sub(r'内容未完，[^\n]+', '', text)
        text = re.sub(r'还不赶快来体验！+', '', text)
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return '\n\n'.join(lines)

    def parse_page(self, html):
        soup = BeautifulSoup(html, 'lxml')
        
        title_tag = soup.find('h1') or soup.find('title')
        title_text = title_tag.get_text().strip() if title_tag else "未知章节"
        
        # 提取章节标题（排除小说名）
        chapter_title = title_text.split('-')[0].strip()
        chapter_title = re.sub(r'^.*?章', '', chapter_title).strip()  # 去除可能的小说名前缀
        
        content_div = soup.find('div', {'id': 'novelcontent'})
        if not content_div:
            raise ValueError("未找到章节内容")
        
        for ad in content_div.find_all(['script', 'font', 'div']):
            ad.decompose()
        
        raw_text = content_div.get_text(separator='\n')
        cleaned_content = self.clean_content(raw_text)
        
        next_url = None
        page_nav = soup.find('div', {'class': 'page_chapter'})
        if page_nav:
            next_btn = page_nav.find('a', string=re.compile(r'下一頁|下一页|下一章'))
            if next_btn and 'href' in next_btn.attrs:
                next_url = urljoin(self.current_url, next_btn['href'])
        
        return {
            'title': self.sanitize_filename(chapter_title),
            'content': cleaned_content,
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
            print(f"❌ 权限不足，请运行: termux-setup-storage 并允许文件访问")
            self.show_notification("下载失败", "存储权限不足")
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
            print("⚠️ 没有可合并的章节文件")
            return
        
        # 生成合并文件名
        if self.novel_name:
            base_name = self.novel_name
        else:
            base_name = f"小说_{time.strftime('%Y%m%d%H%M%S')}"
        merged_file = os.path.join(config['save_path'], f"{base_name}.txt")
        
        try:
            with open(merged_file, 'w', encoding='utf-8') as mf:
                total = len(chapter_files)
                for i, cf in enumerate(chapter_files, 1):
                    path = os.path.join(config['save_path'], cf)
                    with open(path, 'r', encoding='utf-8') as sf:
                        mf.write(sf.read() + '\n\n')
                    print(f"📖 合并进度: {i}/{total} - {cf}")
            
            deleted_count = 0
            for cf in chapter_files:
                file_path = os.path.join(config['save_path'], cf)
                try:
                    os.remove(file_path)
                    deleted_count += 1
                    print(f"🗑️ 已删除: {cf}")
                except Exception as e:
                    print(f"❌ 删除文件 {cf} 失败: {str(e)}")
            
            print(f"✅ 合并完成: {merged_file}")
            self.show_notification("合并完成", f"文件已保存为: {os.path.basename(merged_file)}")
            return merged_file
        except Exception as e:
            print(f"❌ 合并失败: {str(e)}")
            self.show_notification("合并失败", str(e))
            return None

    def download_all(self, merge_after=False):
        print("🏁 开始下载，按Ctrl+C停止")
        try:
            while self.current_url:
                print(f"📡 正在抓取: {self.current_url}")
                html = self.get_page_content(self.current_url)
                if not html:
                    print("🚨 页面加载失败，跳过本章节")
                    break
                
                try:
                    data = self.parse_page(html)
                    if self.save_chapter(data['title'], data['content']):
                        self.current_url = data['next_url']
                    else:
                        break
                except Exception as e:
                    print(f"🚨 解析失败: {str(e)}")
                    self.show_notification("解析错误", str(e))
                    break
                
                time.sleep(config['request_interval'])
            
            print("🎉 下载任务完成")
            if merge_after:
                self.merge_chapters()
            self.show_notification("下载完成", f"文件保存在：{config['save_path']}")
            
        except KeyboardInterrupt:
            print("\n🛑 用户中断下载")
            self.show_notification("下载中断", "已保存已下载章节")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='笔趣阁小说下载器 - Termux版')
    parser.add_argument('url', nargs='?', help='起始章节URL')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-d', '--download-only', action='store_true', help='仅下载不合并')
    group.add_argument('-m', '--merge-only', action='store_true', help='仅合并已下载文件')
    
    args = parser.parse_args()

    if args.merge_only:
        dl = BiqugeDownloader()
        dl.merge_chapters()
    elif args.download_only or args.url:
        if not args.url:
            parser.error("需要提供起始URL")
        dl = BiqugeDownloader(args.url)
        dl.download_all(merge_after=not args.download_only)
    else:
        parser.print_help()
        print("\n示例用法:")
        print("  完整下载: python novel_dl.py https://m.biquguaxs.com/311/311656/48179016.html")
        print("  仅下载:   python novel_dl.py URL --download-only")
        print("  仅合并:   python novel_dl.py --merge-only")
