import requests
from bs4 import BeautifulSoup
import time
import os
import re
import subprocess
import argparse

config = {
    'headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36'
    },
    'save_path': '/storage/emulated/0/Download/novels',
    'request_interval': 3,
    'max_retries': 5,
    'termux_notify': True
}

class TermuxNovelDownloader:
    def __init__(self, start_url=None):
        self.start_url = start_url
        self.current_url = start_url
        self.session = requests.Session()
        self.session.headers.update(config['headers'])
        self.chapter_count = 0
        os.makedirs(config['save_path'], exist_ok=True)
        self.is_termux = 'com.termux' in os.getcwd()

    def show_notification(self, title, message):
        if self.is_termux and config['termux_notify']:
            try:
                subprocess.run([
                    'termux-notification',
                    '--title', title,
                    '--content', message
                ])
            except FileNotFoundError:
                print("未找到termux-notification，请先安装termux-api")

    def sanitize_filename(self, name):
        return re.sub(r'[\\/:*?"<>|]', '', name).strip()

    def get_page_content(self, url):
        for retry in range(config['max_retries']):
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                return response.text
            except Exception as e:
                print(f"请求失败({retry+1}/{config['max_retries']}): {str(e)}")
                time.sleep(2)
        return None

    def parse_page(self, html):
        soup = BeautifulSoup(html, 'lxml')
        title_tag = soup.find('title')
        chapter_title = title_tag.text.split('_')[0] if title_tag else "未知章节"
        clean_title = self.sanitize_filename(chapter_title)
        
        content_div = soup.find('div', {'id': 'chaptercontent'})
        if not content_div:
            raise ValueError("未找到章节内容")
        
        paragraphs = []
        for p in content_div.find_all('p'):
            text = p.text.strip()
            if text and not text.startswith('<!--'):
                paragraphs.append(text)
        
        next_link = soup.find('a', {'id': 'pt_next'})
        next_url = None
        if next_link and 'href' in next_link.attrs and '没有了' not in next_link.text:
            next_url = requests.compat.urljoin(self.current_url, next_link['href'])
        
        return {
            'title': clean_title,
            'content': '\n\n'.join(paragraphs),
            'next_url': next_url
        }

    def save_chapter(self, title, content):
        self.chapter_count += 1
        filename = os.path.join(config['save_path'], f"{self.chapter_count:03d}_{title}.txt")
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"✅ 已保存: {filename}")
        except PermissionError:
            print(f"❌ 权限不足，请运行: termux-setup-storage 并允许文件访问")
            self.show_notification("下载失败", "存储权限不足")
            exit(1)

    def merge_chapters(self):
        chapter_files = sorted(
            [f for f in os.listdir(config['save_path']) 
             if re.match(r'^\d{3}_', f) and f.endswith('.txt')],
            key=lambda x: int(x.split('_')[0])
        )
        
        if not chapter_files:
            print("⚠️ 没有可合并的章节文件")
            return
        
        merged_file = os.path.join(config['save_path'], "merged_novel.txt")
        try:
            files_to_delete = [os.path.join(config['save_path'], cf) for cf in chapter_files]
            
            with open(merged_file, 'w', encoding='utf-8') as mf:
                for cf in chapter_files:
                    with open(os.path.join(config['save_path'], cf), 'r', encoding='utf-8') as sf:
                        mf.write(sf.read() + '\n\n')
            
            deleted_count = 0
            for fpath in files_to_delete:
                try:
                    os.remove(fpath)
                    deleted_count += 1
                    print(f"🗑️ 已删除: {os.path.basename(fpath)}")
                except Exception as e:
                    print(f"❌ 删除失败 {os.path.basename(fpath)}: {str(e)}")
            
            print(f"✅ 合并完成: {merged_file}")
            print(f"🗑️ 已清理 {deleted_count}/{len(chapter_files)} 个章节文件")
            self.show_notification("合并完成", 
                                f"最终文件: {merged_file}\n清理文件: {deleted_count}个")
        except Exception as e:
            print(f"❌ 合并失败: {str(e)}")
            self.show_notification("合并失败", str(e))

    def download_all(self, merge_after=False):
        print("🏁 开始下载，按Ctrl+C停止")
        try:
            while self.current_url:
                print(f"📖 正在下载: {self.current_url}")
                html = self.get_page_content(self.current_url)
                if not html:
                    print("🚨 页面加载失败，跳过本章节")
                    self.current_url = None
                    break
                
                try:
                    data = self.parse_page(html)
                    self.save_chapter(data['title'], data['content'])
                    self.current_url = data['next_url']
                except Exception as e:
                    print(f"🚨 解析失败: {str(e)}")
                    self.show_notification("下载错误", str(e))
                    break
                
                time.sleep(config['request_interval'])
            
            print("🎉 所有章节下载完成！")
            if merge_after:
                self.merge_chapters()
            self.show_notification("下载完成", f"文件保存在：{config['save_path']}")
            
        except KeyboardInterrupt:
            print("\n🛑 用户中断下载")
            self.show_notification("下载中断", "用户主动停止")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Termux小说下载工具')
    parser.add_argument('url', nargs='?', help='小说起始章节的URL')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--download-only', action='store_true', help='仅下载章节不合并')
    group.add_argument('--merge-only', action='store_true', help='仅合并已下载章节')
    
    args = parser.parse_args()

    if args.merge_only:
        downloader = TermuxNovelDownloader()
        downloader.merge_chapters()
    elif args.download_only or args.url:
        if not args.url:
            parser.error("下载模式需要提供起始URL")
        downloader = TermuxNovelDownloader(args.url)
        downloader.download_all(merge_after=not args.download_only)
    else:
        parser.print_help()
        print("\n⚠️ 请选择运行模式：")
        print("1. 下载并合并: python script.py <起始URL>")
        print("2. 仅下载: python script.py <起始URL> --download-only")
        print("3. 仅合并: python script.py --merge-only")
