import os
import oss2
import json
import time
import requests
from requests.exceptions import Timeout
from config import conf
from github import Github
from datetime import datetime

class Weibo(object):
    def __init__(self):
        self.cookie = conf.get('Cookie')
        self.header = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.13; rv:61.0) Gecko/20100101 Firefox/61.0',
            }
        self.url_pattern = 'https://api.weibo.cn/2/profile/statuses/tab?from=10AA393010&c=iphone&s=0ddddddd&containerid=2304136826971661_-_WEIBO_SECOND_PROFILE_WEIBO&count=20&page={}'
        self.url_detail_pattern = 'https://api.weibo.cn/2/statuses/show?from=10AB193010&c=iphone&s=2c81ab39&id={}'
        # 设置Cookie
        if self.cookie:
            self.header['Cookie'] = self.cookie
        # 初始化历史推文ID记录
        self.history_ids = self.init_history_ids()
        # 初始化OSS相关对象
        self.init_oss()
        self.new_cards = []
        self.new_images = []
        self.github = None
        self.repo = None
        pass

    # init tweets history ids
    def init_history_ids(self, retry=3, timeout=20):
        url = conf.get('History_Tweets_ids')
        if not url:
            return {}
        url = url + '?time=' + str(int(time.time()*1000))
        # 尝试[retry]次下载内容
        resp = None
        for i in range(retry):
            try:
                resp = requests.get(
                    url,
                    headers=self.header,
                    timeout=timeout
                )
                break
            except Exception as e:
                print(e)
        ret = {}
        if resp and resp.status_code:
            if resp.status_code != 200:
                print('资源同步成功, 但是服务器返回了异常状态')
            else:
                print('资源下载成功')
                ret = json.loads(resp.content.decode('UTF-8'))
        else:
            print('资源下载尝试超出最大尝试次数.')
        return ret

    # init oss
    def init_oss(self):
        oss = conf.get('OSS')
        auth = oss2.Auth(oss.get('A_K'), oss.get('A_S'))
        self.bucket = oss2.Bucket(auth, oss.get('Region'), oss.get('Bucket'))
        return True

    # 开启微博备份, 该备份建议是小数目备份
    def start_backup(self, timeout=20):
        page = 1
        new_cards = []
        while True:
            try:
                resp = requests.get(
                    self.url_pattern.format(page),
                    headers=self.header,
                    timeout=timeout
                )
                if resp.status_code != 200:
                    print('服务器状态异常')
                else:
                    # 解析服务器的响应数据
                    content = json.loads(resp.content.decode('UTF-8'))
                    if 'cards' not in content.keys():
                        # 如果服务器响应了异常的内容
                        print(content.get('errmsg'))
                        print('服务器恢复了错误响应, 可能是由于Cookie到期')
                        return []
                    else:
                        # 期望的请求内容
                        cards = [_ for _ in content.get('cards') if _.get('card_type') == 9]
                        # ret = []
                        history_str = json.dumps(self.history_ids, ensure_ascii=False)
                        for card in cards:
                            i = card.get('mblog').get('idstr')
                            if i not in history_str:
                                new_cards.append(card)
                            else:
                                print('已经找到上次的备份头,数据卡片同步完成')
                                self.new_cards = new_cards
                                return new_cards
                    page += 1
                    print('还没找到上次的备份头')
            except Exception as e:
                print(e)
                pass
        return []

    # 带header与cookie的请求
    def requests(self, url, retry=3, timeout=10):
        resp = None
        for i in range(retry):
            try:
                resp = requests.get(
                    url,
                    headers=self.header,
                    timeout=timeout
                )
            except:
                continue
        if resp and resp.status_code == 200:
            return resp.content.decode('UTF-8')
        return None


    # 更新cards详情
    def update_cards(self, cards):
        new_cards = []
        for card in cards:
            blog = card.get('mblog')
            # 如果为转发微博的话
            if blog.get('retweeted_status'):
                bid = blog.get('retweeted_status').get('id')
                url = self.url_detail_pattern.format(bid)
                content = self.requests(url)
                if content:
                    try:
                        detail = json.loads(content)
                        blog['retweeted_status'] = detail
                    except:
                        pass
            # 如果为原创微博
            else:
                bid = blog.get('id')
                url = self.url_detail_pattern.format(bid)
                content = self.requests(url)
                if content:
                    try:
                        detail = json.loads(content)
                        card['mblog'] = detail
                    except:
                        pass
            new_cards.append(card)
        return new_cards

    # 更新历史数据
    def update_history(self, new_cards, key="history.txt"):
        head = self.bucket.head_object(key)
        key_size = head.headers.get('Content-Length')
        resp = self.bucket.append_object(
                key, 
                key_size, 
                ', ' + json.dumps(new_cards, ensure_ascii=False)[1:-1]
            )
        return True if resp.status == 200 else False

    # 同步ID列表
    def sync_bid_list(self, data, key="list.json"):
        try:
            TOKEN = conf.get('Github').get('Token')
            REPO = "tylzh97/weibo-backup"
            g = Github(TOKEN)
            repo = g.get_repo(REPO)
            # 尝试变量复用
            self.github = g
            self.repo = repo
            # ID文件位置
            # key = 'list.json'
            # 远程内容对象
            remote_contents = repo.get_contents(key, ref="main")
            # 请求更新ID内容
            resp = repo.update_file(
                path=remote_contents.path,
                # 提交信息
                message="更新列表: " + str(datetime.now()),
                # 字符串, 使用base64编码
                content=data,
                sha=remote_contents.sha,
                branch="main"
            )
            # 文件的raw链接
            url = resp.get('content').download_url
            print('ID列表同步成功:\t', url)
            return url
        except Exception as e:
            print('向github中同步id列表时出现异常,错误内容如下:')
            print(e)
        return ''

    # 备份图片到OSS
    def sync_image(self):
        images = []
        print('-----'*10)
        for card in self.new_cards:
            mblog = card.get('mblog')
            if mblog.get('retweeted_status'):
                mblog = mblog.get('retweeted_status')
            if mblog.get('pic_ids'):
                for image in mblog.get('pic_infos').keys():
                    images.append(mblog.get('pic_infos').get(image).get('largest').get('url'))
        images = sorted(list(set(images)))
        self.new_images = images
        counter = 0
        length = len(images)
        for img in images:
            counter += 1
            print(counter, ' / ', length)
            print(img)
            key = 'image/' + img.split('/')[-1]
            content = None
            try:
                img_resp = requests.get(img, timeout=40)
                if img_resp.status_code == 200:
                    content = img_resp.content
                    print(key, '下载完成! ', content[:30])
                else:
                    continue
            except:
                continue
            if not self.bucket.object_exists(key):
                self.bucket.put_object(
                    key, 
                    content
                    )
        return True

    # weixin alert
    def weixin_alert(self, content):
        if not conf.get('SC_KEY'):
            return True
        url = "https://sc.ftqq.com/{KEY}.send?text={TEXT}&desp={DESP}".format(
            KEY=conf.get('SC_KEY'),
            TEXT=content.get('title'),
            DESP=content.get('context')
        )
        requests.get(url)
        return True

    # 开始工作
    def start(self):
        if not self.history_ids:
            print('获取ID异常, 备份结束')
            return False
        # 开始备份, 获取所有的new_cards
        new_cards = self.start_backup()
        new_cards = self.update_cards(new_cards)
        self.new_cards = new_cards
        if not new_cards:
            print('没有发布新微博, 或者卡片列表异常. 备份结束')
            return False
        # 同步照片列表
        self.sync_image()
        # 更新历史记录
        self.update_history(new_cards)
        # 更新ID记录
        new_id_list = [_.get('mblog').get('id') for _ in new_cards]
        print(new_id_list)
        id_list = new_id_list + self.history_ids
        self.sync_bid_list(json.dumps(id_list, ensure_ascii=False))
        self.weixin_alert({
            "title": "微博备份成功",
            "context": "共备份了:\n" + str(len(new_id_list)) + "  条微博,\n" + str(len(self.new_images)) + "  张照片, \n备份列表: \n\n" + json.dumps(new_id_list, ensure_ascii=False),
        })
        return True

if __name__ == '__main__':
    # print(conf)
    w = Weibo()
    w.start()
    # data = w.init_history()
    # print('*'*100)
    # new_cards = w.start_backup()
    # print(len(new_cards))
    # print(data)
    # w.sync_image()
    # print('Hello World!')
    # 11111111111111
    # 22222222222222
    # 3333
    # 4444
