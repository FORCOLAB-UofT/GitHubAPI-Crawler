import time
from datetime import datetime
import json
from typing import Iterable
from random import randint
import os.path
from util import language_tool
import fetch_raw_diff
import logging
import os.path
import init
from fetch_raw_diff import *
from util import localfile

# try:
#     import settings
# except ImportError:
#     settings = object()

# _tokens = getattr(settings, "SCRAPER_GITHUB_API_TOKENS", [])

with open(init.currentDIR+"/data/token.txt", 'r') as file:
    _tokens = [line.rstrip('\n') for line in file]

logger = logging.getLogger('ghd.scraper')

LOCAL_DATA_PATH = init.LOCAL_DATA_PATH
file_list_cache = {}





logger = logging.getLogger('INTRUDE.scraper')
# nonCodeFileExtensionList = [line.rstrip('\n') for line in open('./data/NonCodeFile.txt')]
nonCodeFileExtensionList = [line.rstrip('\n') for line in open(init.currentDIR+'/data/NonCodeFile.txt')]

class RepoDoesNotExist(requests.HTTPError):
    pass


class TokenNotReady(requests.HTTPError):
    pass


def parse_commit(commit):
    github_author = commit['author'] or {}
    commit_author = commit['commit'].get('author') or {}
    return {
        'sha': commit['sha'],
        'author': github_author.get('login'),
        'author_name': commit_author.get('name'),
        'author_email': commit_author.get('email'),
        'authored_date': commit_author.get('date'),
        'message': commit['commit']['message'].replace("\n", ","),
        'committed_date': commit['commit']['committer']['date'],
        'parents': "\n".join(p['sha'] for p in commit['parents']),
        'verified': commit.get('verification', {}).get('verified')
    }


class GitHubAPIToken(object):
    api_url = "https://api.github.com/"

    token = None
    timeout = None
    _user = None
    _headers = None

    limit = None  # see __init__ for more details

    def __init__(self, token=None, timeout=None):
        if token is not None:
            self.token = token
            self._headers = {
                "Authorization": "token " + token,
                # "Accept": "application/vnd.github.v3+json",
                "Accept": "application/vnd.github.mockingbird-preview"
                #                 "User-Agent": "request"
            }
        self.limit = {}
        for api_class in ('core', 'search'):
            self.limit[api_class] = {
                'limit': None,
                'remaining': None,
                'reset_time': None
            }
        self.timeout = timeout
        super(GitHubAPIToken, self).__init__()

    @property
    def user(self):
        if self._user is None:
            try:
                r = self.request('user')
            except TokenNotReady:
                pass
            else:
                self._user = r.json().get('login', '')
        return self._user

    def _check_limits(self):
        # regular limits will be updaated automatically upon request
        # we only need to take care about search limit
        try:
            s = self.request('rate_limit').json()['resources']['search']
        except TokenNotReady:
            # self.request updated core limits already; search limits unknown
            s = {'remaining': None, 'reset': None, 'limit': None}

        self.limit['search'] = {
            'remaining': s['remaining'],
            'reset_time': s['reset'],
            'limit': s['limit']
        }

    @staticmethod
    def api_class(url):
        return 'search' if url.startswith('search') else 'core'

    def ready(self, url):
        t = self.when(url)
        return not t or t <= time.time()

    def legit(self):
        if self.limit['core']['limit'] is None:
            self._check_limits()
        return self.limit['core']['limit'] < 100

    def when(self, url):
        key = self.api_class(url)
        if self.limit[key]['remaining'] != 0:
            return 0
        return self.limit[key]['reset_time']

    def request(self, url, method='get', data=None, **params):
        # TODO: use coroutines, perhaps Tornado (as PY2/3 compatible)

        if not self.ready(url):
            raise TokenNotReady
        # Exact API version can be specified by Accept header:
        # "Accept": "application/vnd.github.v3+json"}

        # might throw a timeout
        r = requests.request(
            method, self.api_url + url, params=params, data=data,
            headers=self._headers, timeout=self.timeout)

        if 'X-RateLimit-Remaining' in r.headers:
            remaining = int(r.headers['X-RateLimit-Remaining'])
            self.limit[self.api_class(url)] = {
                'remaining': remaining,
                'reset_time': int(r.headers['X-RateLimit-Reset']),
                'limit': int(r.headers['X-RateLimit-Limit'])
            }

            if r.status_code == 403 and remaining == 0:
                raise TokenNotReady
            if r.status_code == 443:
                print('443 error')
                raise TokenNotReady
        return r


class GitHubAPI(object):
    """ This is a convenience class to pool GitHub API keys and update their
    limits after every request. Actual work is done by outside classes, such
    as _IssueIterator and _CommitIterator
    """
    _instance = None  # instance of API() for Singleton pattern implementation
    tokens = None

    def __new__(cls, *args, **kwargs):  # Singleton
        if not isinstance(cls._instance, cls):
            cls._instance = super(GitHubAPI, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self, tokens=_tokens, timeout=30):
        if not tokens:
            raise EnvironmentError(
                "No GitHub API tokens found in settings.py. Please add some.")
        self.tokens = [GitHubAPIToken(t, timeout=timeout) for t in tokens]

    def requestPR(self, url, method='get', page=1, data=None, **params):
        # type: (str, str, bool, str) -> dict
        """ Generic, API version agnostic request method """
        timeout_counter = 0
        params['page'] = page
        params['per_page'] = init.numPRperPage
        while True:
            for token in self.tokens:
                # for token in sorted(self.tokens, key=lambda t: t.when(url)):
                if not token.ready(url):
                    continue

                try:
                    r = token.request(url, method=method, data=data, **params)
                    # print(r.url)
                except requests.ConnectionError:
                    print('except requests.ConnectionError')
                    continue
                except TokenNotReady:
                    continue
                except requests.exceptions.Timeout:
                    timeout_counter += 1
                    if timeout_counter > len(self.tokens):
                        raise
                    continue  # i.e. try again

                if r.status_code in (404, 451):
                    print("404, 451 retry..")
                    return {}
                    # API v3 only
                    # raise RepoDoesNotExist(
                    #     "GH API returned status %s" % r.status_code)
                elif r.status_code == 409:
                    print("409 retry..")
                    # repository is empty https://developer.github.com/v3/git/
                    return {}
                elif r.status_code == 410:
                    print("410 retry..")
                    # repository is empty https://developer.github.com/v3/git/
                    return {}
                elif r.status_code == 401:
                    print("401,Bad credentials, please remove this token")
                    continue
                elif r.status_code == 403:
                    # repository is empty https://developer.github.com/v3/git/
                    print("403 retry..")
                    time.sleep(randint(1, 60))
                    continue
                elif r.status_code == 443:
                    # repository is empty https://developer.github.com/v3/git/
                    print("443 retry..")
                    time.sleep(randint(1, 29))
                    continue
                elif r.status_code == 502:
                    # repository is empty https://developer.github.com/v3/git/
                    print("443 retry..")
                    time.sleep(randint(1, 29))
                    continue
                r.raise_for_status()
                res = r.json()
                return res

            next_res = min(token.when(url) for token in self.tokens)
            sleep = int(next_res - time.time()) + 1
            if sleep > 0:
                logger.info(
                    "%s: out of keys, resuming in %d minutes, %d seconds",
                    datetime.now().strftime("%H:%M"), *divmod(sleep, 60))
                time.sleep(sleep)
                logger.info(".. resumed")

    def request(self, url, method='get', paginate=False, data=None, **params):
        # type: (str, str, bool, str) -> dict
        """ Generic, API version agnostic request method """

        timeout_counter = 0
        if paginate:
            paginated_res = []
            params['page'] = 1
            params['per_page'] = 100

        while True:
            for token in self.tokens:
                # for token in sorted(self.tokens, key=lambda t: t.when(url)):
                if not token.ready(url):
                    continue

                try:
                    r = token.request(url, method=method, data=data, **params)
                    # print(r.url)
                except requests.ConnectionError:
                    print('except requests.ConnectionError')
                    continue
                except TokenNotReady:
                    continue
                except requests.exceptions.Timeout:
                    timeout_counter += 1
                    if timeout_counter > len(self.tokens):
                        raise
                    continue  # i.e. try again

                if r.status_code in (404, 451):
                    print("404, 451 retry..")
                    return {}
                    # API v3 only
                    # raise RepoDoesNotExist(
                    #     "GH API returned status %s" % r.status_code)
                elif r.status_code == 409:
                    print("409 retry..")
                    # repository is empty https://developer.github.com/v3/git/
                    return {}
                elif r.status_code == 410:
                    print("410 retry..")
                    # repository is empty https://developer.github.com/v3/git/
                    return {}
                elif r.status_code == 401:
                    print("401,Bad credentials, please remove this token")
                    continue
                elif r.status_code == 403:
                    # repository is empty https://developer.github.com/v3/git/
                    print("403 retry..")
                    time.sleep(randint(1, 60))
                    continue
                elif r.status_code == 443:
                    # repository is empty https://developer.github.com/v3/git/
                    print("443 retry..")
                    time.sleep(randint(1, 29))
                    continue
                elif r.status_code == 502:
                    # repository is empty https://developer.github.com/v3/git/
                    print("502 retry..")
                    time.sleep(randint(1, 29))
                    continue
                elif r.status_code == 500:
                    # repository is empty https://developer.github.com/v3/git/
                    print("500 retry..")
                    time.sleep(randint(1, 29))
                    continue
                r.raise_for_status()
                res = r.json()
                if paginate:
                    paginated_res.extend(res)
                    has_next = 'rel="next"' in r.headers.get("Link", "")
                    if not res or not has_next:
                        return paginated_res
                    else:
                        params["page"] += 1
                        continue
                else:
                    return res

            next_res = min(token.when(url) for token in self.tokens)
            sleep = int(next_res - time.time()) + 1
            if sleep > 0:
                logger.info(
                    "%s: out of keys, resuming in %d minutes, %d seconds",
                    datetime.now().strftime("%H:%M"), *divmod(sleep, 60))
                time.sleep(sleep)
                logger.info(".. resumed")

    def repo_issues(self, repo_name, page=None):
        # type: (str, int) -> Iterable[dict]
        url = "repos/%s/issues" % repo_name

        if page is None:
            data = self.request(url, paginate=True, state='all')
        else:
            data = self.request(url, page=page, per_page=100, state='all')

        for issue in data:
            if 'pull_request' not in issue:
                yield {
                    'author': issue['user']['login'],
                    'closed': issue['state'] != "open",
                    'created_at': issue['created_at'],
                    'updated_at': issue['updated_at'],
                    'closed_at': issue['closed_at'],
                    'number': issue['number'],
                    'title': issue['title']
                }

    def repo_commits(self, repo_name):

        url = "repos/%s/commits" % repo_name

        for commit in self.request(url, paginate=True):
            # might be None for commits authored outside of github
            yield parse_commit(commit)

        url = "repos/%s/pulls" % repo_name

        for pr in self.request(url, paginate=True, state='all'):
            body = pr.get('body', {})
            head = pr.get('head', {})
            head_repo = head.get('repo') or {}
            base = pr.get('base', {})
            base_repo = base.get('repo') or {}

            yield {
                'id': int(pr['number']),  # no idea what is in the id field
                'title': pr['title'],
                'body': body,
                'labels': 'labels' in pr and [l['name'] for l in pr['labels']],
                'created_at': pr['created_at'],
                'updated_at': pr['updated_at'],
                'closed_at': pr['closed_at'],
                'merged_at': pr['merged_at'],
                'author': pr['user']['login'],
                'head': head_repo.get('full_name'),
                'head_branch': head.get('label'),
                'base': base_repo.get('full_name'),
                'base_branch': base.get('label'),
            }

    def pr_status(self, repo, pr_id):
        url = "repos/%s/pulls/%s" % (repo, pr_id)
        pr = self.request(url)
        return pr['state']

    def pull_request_commits(self, repo, pr_id):
        # type: (str, int) -> Iterable[dict]
        url = "repos/%s/pulls/%d/commits" % (repo, pr_id)

        for commit in self.request(url, paginate=True, state='all'):
            yield parse_commit(commit)

    def issue_comments(self, repo, issue_id):
        """ Return comments on an issue or a pull request
        Note that for pull requests this method will return only general
        comments to the pull request, but not review comments related to
        some code. Use review_comments() to get those instead

        :param repo: str 'owner/repo'
        :param issue_id: int, either an issue or a Pull Request id
        """
        url = "repos/%s/issues/%s/comments" % (repo, issue_id)

        for comment in self.request(url, paginate=True, state='all'):
            yield {
                'body': comment['body'],
                'author': comment['user']['login'],
                'created_at': comment['created_at'],
                'updated_at': comment['updated_at'],
            }

    def get_issue_pr_timeline(self, repo, issue_id):
        """ Return timeline on an issue or a pull request
        :param repo: str 'owner/repo'url
        :param issue_id: int, either an issue or a Pull Request id
        """
        url = "repos/%s/issues/%s/timeline" % (repo, issue_id)
        # print(url)
        events = self.request(url, paginate=True, state='all')
        return events

    def issue_pr_timeline(self, repo, issue_id):
        """ Return timeline on an issue or a pull request
        :param repo: str 'owner/repo'url
        :param issue_id: int, either an issue or a Pull Request id
        """
        url = "repos/%s/issues/%s/timeline" % (repo, issue_id)
        events = self.request(url, paginate=True, state='all')
        for event in events:
            # print('repo: ' + repo + ' issue: ' + str(issue_id) + ' event: ' + event['event'])
            if event['event'] == 'cross-referenced':
                author = event['actor'] or {}
                yield {
                    'event': event['event'],
                    'author': author.get('login'),
                    'email': '',
                    'author_type': author.get('type'),
                    'author_association': '',
                    'commit_id': "",
                    'created_at': event.get('created_at'),
                    'id': event['source']['issue']['number'],
                    'repo': event['source']['issue']['repository']['full_name'],
                    'type': 'pull_request' if 'pull_request' in event['source']['issue'].keys() else 'issue',
                    'state': event['source']['issue']['state'],
                    'assignees': event['source']['issue']['assignees'],
                    'label': "",
                    'body': ''
                }
            elif event['event'] == 'referenced':
                author = event['actor'] or {}
                yield {
                    'event': event['event'],
                    'author': author.get('login'),
                    'email': '',
                    'author_type': author.get('type'),
                    'author_association': '',
                    'commit_id': event['commit_id'],
                    'created_at': event['created_at'],
                    'id': '',
                    'repo': '',
                    'type': 'commit',
                    'state': '',
                    'assignees': '',
                    'label': '',
                    'body': ''
                }
            elif event['event'] == 'labeled':
                author = event['actor'] or {}
                yield {
                    'event': event['event'],
                    'author': author.get('login'),
                    'email': '',
                    'author_type': author.get('type'),
                    'author_association': '',
                    'commit_id': '',
                    'created_at': event.get('created_at'),
                    'id': '',
                    'repo': '',
                    'type': "label",
                    'state': '',
                    'assignees': '',
                    'label': event['label']['name'],
                    'body': ''
                }
            elif event['event'] == 'committed':
                yield {
                    'event': event['event'],
                    'author': event['author']['name'],
                    'email': event['author']['email'],
                    'author_type': '',
                    'author_association': '',
                    'commit_id': event['sha'],
                    'created_at': event.get('created_at'),
                    'id': '',
                    'repo': '',
                    'type': "commit",
                    'state': '',
                    'assignees': '',
                    'label': '',
                    'body': ''
                }
            elif event['event'] == 'reviewed':
                author = event['user'] or {}
                yield {
                    'event': event['event'],
                    'author': author.get('login'),
                    'email': '',
                    'author_type': author.get('type'),
                    'author_association': event['author_association'],
                    'commit_id': '',
                    'created_at': event.get('created_at'),
                    'id': '',
                    'repo': '',
                    'type': "review",
                    'state': event['state'],
                    'assignees': '',
                    'label': '',
                    'body': ''
                }
            elif event['event'] == 'commented':
                yield {
                    'event': event['event'],
                    'author': event['user']['login'],
                    'email': '',
                    'author_type': event['user']['type'],
                    'author_association': event['author_association'],
                    'commit_id': '',
                    'created_at': event.get('created_at'),
                    'id': '',
                    'repo': '',
                    'type': "comment",
                    'state': '',
                    'assignees': '',
                    'label': '',
                    'body': event['body']
                }
            elif event['event'] == 'assigned':
                author = event['actor'] or {}
                yield {
                    'event': event['event'],
                    'author': author.get('login'),
                    'email': '',
                    'author_type': author.get('type'),
                    'author_association': '',
                    'commit_id': '',
                    'created_at': event.get('created_at'),
                    'id': '',
                    'repo': '',
                    'type': "comment",
                    'state': '',
                    'assignees': '',
                    'label': '',
                    'body': ''
                }
            elif event['event'] == 'closed':
                author = event['actor'] or {}
                yield {
                    'event': event['event'],
                    'author': author.get('login'),
                    'email': '',
                    'author_type': author.get('type'),
                    'author_association': '',
                    'commit_id': event['commit_id'],
                    'created_at': event.get('created_at'),
                    'id': '',
                    'repo': '',
                    'type': "close",
                    'state': '',
                    'assignees': '',
                    'label': '',
                    'body': ''
                }
            elif event['event'] == 'subscribed':
                author = event['actor'] or {}
                yield {
                    'event': event['event'],
                    'author': author.get('login'),
                    'email': '',
                    'author_type': author.get('type'),
                    'author_association': '',
                    'commit_id': event['commit_id'],
                    'created_at': event.get('created_at'),
                    'id': event['commit_id'],
                    'repo': '',
                    'type': "subscribed",
                    'state': '',
                    'assignees': '',
                    'label': '',
                    'body': ''
                }
            elif event['event'] == 'merged':
                author = event['actor'] or {}
                yield {
                    'event': event['event'],
                    'author': author.get('login'),
                    'email': '',
                    'author_type': author.get('type'),
                    'author_association': '',
                    'commit_id': event['commit_id'],
                    'created_at': event.get('created_at'),
                    'id': event['commit_id'],
                    'repo': '',
                    'type': "merged",
                    'state': '',
                    'assignees': '',
                    'label': '',
                    'body': ''
                }
            else:
                yield {
                    'event': event['event'],
                    'author': '',
                    'email': '',
                    'author_type': '',
                    'author_association': '',
                    'commit_id': '',
                    'created_at': event.get('created_at'),
                    'id': '',
                    'repo': '',
                    'type': "",
                    'state': '',
                    'assignees': '',
                    'label': '',
                    'body': ''
                }

    def pr_changedFiles(self, repo, pr_id):
        """ Return changed file list on an issue or a pull request
        :param repo: str 'owner/repo'url
        :param pr_id: int,  Pull Request id
        """
        url = "repos/%s/pulls/%s/files" % (repo, pr_id)
        files = self.request(url, paginate=True, state='all')
        for file in files:
            # print('repo: ' + repo + ' issue: ' + str(issue_id) + ' event: ' + event['event'])

            yield {
                'filename': file['filename'],
                'status': file['status'],
                'additions': file['additions'],
                'deletions': file['deletions'],
                'changes': file['changes'],
                'blob_url': file['blob_url'],
                'raw_url': file['raw_url'],
                'contents_url': file['contents_url']
            }

    def commit_changedFile(self, repo, sha):
        """ Return changed file list on an issue or a pull request
        :param repo: str 'owner/repo'url
        :param sha,
        """

        url = "repos/%s/commits/%s" % (repo, sha)
        commitInfo = self.request(url)
        files = commitInfo['files']
        for file in files:
            yield {
                'filename': file['filename'],
                'status': file['status'],
                'additions': file['additions'],
                'deletions': file['deletions'],
                'changes': file['changes']
            }

    def repoLastPushDate(self, repoUrl):
        url = "repos/%s" % (repoUrl)
        repoInfo = self.request(url)
        if (len(repoInfo) == 0):
            print(repoUrl + " deleted")
            return ''
        else:
            return repoInfo['pushed_at']

    def userEmail(self, loginID):
        """ Return changed file list on an issue or a pull request
        :param repo: str 'owner/repo'url
        :param sha,
        """
        url = "users/%s" % (loginID)
        userInfo = self.request(url)
        if (len(userInfo) == 0):
            print(loginID + " deleted")
            return ''
        else:
            email = userInfo['email']
            return email


    # this function get repo by specifying constraints
    def get_repo(self, language, created_date_from, created_date_to):
        """ Return timeline on an issue or a pull request
                :param repo: str 'owner/repo'url
                :param issue_id: int, either an issue or a Pull Request id
                """
        url = 'search/repositories?q=language%3A\"'+language+'\"+created%3A'+created_date_from+'..'+created_date_to
        print(url)
        repos = self.request(url, paginate=False)
        return repos


def review_comments(self, repo, pr_id):
    """ Pull request comments attached to some code
    See also issue_comments()
    """
    url = "repos/%s/pulls/%s/comments" % (repo, pr_id)

    for comment in self.request(url, paginate=True, state='all'):
        yield {
            'id': comment['id'],
            'body': comment['body'],
            'author': comment['user']['login'],
            'created_at': comment['created_at'],
            'updated_at': comment['updated_at'],
            'author_association': comment['author_association']
        }


def user_info(self, user):
    # Docs: https://developer.github.com/v3/users/#response
    return self.request("users/" + user)


def org_members(self, org):
    # TODO: support pagination
    return self.request("orgs/%s/members" % org)


def user_orgs(self, user):
    # TODO: support pagination
    return self.request("users/%s/orgs" % user)


@staticmethod
def project_exists(repo_name):
    return bool(requests.head("https://github.com/" + repo_name))


@staticmethod
def canonical_url(project_url):
    # type: (str) -> str
    """ Normalize URL
    - remove trailing .git  (IMPORTANT)
    - lowercase (API is insensitive to case, but will allow to deduplicate)
    - prepend "github.com"

    :param project_url: str, user_name/repo_name
    :return: github.com/user_name/repo_name with both names normalized

    >>> GitHubAPI.canonical_url("pandas-DEV/pandas")
    'github.com/pandas-dev/pandas'
    >>> GitHubAPI.canonical_url("http://github.com/django/django.git")
    'github.com/django/django'
    >>> GitHubAPI.canonical_url("https://github.com/A/B/")
    'github.com/a/b/'
    """
    url = project_url.lower()
    for chunk in ("httpp://", "https://", "github.com"):
        if url.startswith(chunk):
            url = url[len(chunk):]
    if url.endswith("/"):
        url = url[:-1]
    while url.endswith(".git"):
        url = url[:-4]
    return "github.com/" + url


@staticmethod
def activity(repo_name):
    # type: (str) -> dict
    """Unofficial method to get top 100 contributors commits by week"""
    url = "https://github.com/%s/graphs/contributors" % repo_name
    headers = {
        'X-Requested-With': 'XMLHttpRequest',
        'Accept-Encoding': "gzip,deflate,br",
        'Accept': "application/json",
        'Origin': 'https://github.com',
        'Referer': url,
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:53.0) "
                      "Gecko/20100101 Firefox/53.0",
        "Host": 'github.com',
        "Accept-Language": 'en-US,en;q=0.5',
        "Connection": "keep-alive",
        "Cache-Control": 'max-age=0',
    }
    cookies = requests.get(url).cookies
    r = requests.get(url + "-data", cookies=cookies, headers=headers)
    r.raise_for_status()
    return r.json()


class GitHubAPIv4(GitHubAPI):
    def v4(self, query, **params):
        # type: (str) -> dict
        payload = json.dumps({"query": query, "variables": params})
        return self.request("graphql", 'post', data=payload)

    def repo_issues(self, repo_name, cursor=None):
        # type: (str, str) -> Iterable[dict]
        owner, repo = repo_name.split("/")
        query = """query ($owner: String!, $repo: String!, $cursor: String) {
        repository(name: $repo, owner: $owner) {
          hasIssuesEnabled
            issues (first: 100, after: $cursor,
              orderBy: {field:CREATED_AT, direction: ASC}) {
                nodes {author {login}, closed, createdAt,
                       updatedAt, number, title}
                pageInfo {endCursor, hasNextPage}
        }}}"""

        while True:
            data = self.v4(query, owner=owner, repo=repo, cursor=cursor
                           )['data']['repository']
            if not data:  # repository is empty, deleted or moved
                break

            for issue in data["issues"]:
                yield {
                    'author': issue['author']['login'],
                    'closed': issue['closed'],
                    'created_at': issue['createdAt'],
                    'updated_at': issue['updatedAt'],
                    'closed_at': None,
                    'number': issue['number'],
                    'title': issue['title']
                }

            cursor = data["issues"]["pageInfo"]["endCursor"]

            if not data["issues"]["pageInfo"]["hasNextPage"]:
                break

    def repo_commits(self, repo_name, cursor=None):
        # type: (str, str) -> Iterable[dict]
        """As of June 2017 GraphQL API does not allow to get commit parents
        Until this issue is fixed this method is only left for a reference
        Please use commits() instead"""
        owner, repo = repo_name.split("/")
        query = """query ($owner: String!, $repo: String!, $cursor: String) {
        repository(name: $repo, owner: $owner) {
          ref(qualifiedName: "master") {
            target { ... on Commit {
              history (first: 100, after: $cursor) {
                nodes {sha:oid, author {name, email, user{login}}
                       message, committedDate}
                pageInfo {endCursor, hasNextPage}
        }}}}}}"""

        while True:
            data = self.v4(query, owner=owner, repo=repo, cursor=cursor
                           )['data']['repository']
            if not data:
                break

            for commit in data["ref"]["target"]["history"]["nodes"]:
                yield {
                    'sha': commit['sha'],
                    'author': commit['author']['user']['login'],
                    'author_name': commit['author']['name'],
                    'author_email': commit['author']['email'],
                    'authored_date': None,
                    'message': commit['message'],
                    'committed_date': commit['committedDate'],
                    'parents': None,
                    'verified': None
                }

            cursor = data["ref"]["target"]["history"]["pageInfo"]["endCursor"]
            if not data["ref"]["target"]["history"]["pageInfo"]["hasNextPage"]:
                break



def fetch_pr_code_info(repo, pr_id, must_in_local=False):
    global file_list_cache
    ind = (repo, pr_id)
    if ind in file_list_cache:
        return file_list_cache[ind]

    path = LOCAL_DATA_PATH + '/pr_data/%s/%s' % (repo, pr_id)
    # if os.path.exists(path + '/toobig.txt'):
    #     return []

    raw_diff_path = path + '/raw_diff.json'
    pull_files_path = path + '/pull_files.json'


    if os.path.exists(raw_diff_path) or os.path.exists(pull_files_path):
        if os.path.exists(raw_diff_path):
            file_list = localfile.get_file(raw_diff_path)
        elif os.path.exists(pull_files_path):
            pull_files = localfile.get_file(pull_files_path)
            file_list = [parse_diff(file["file_full_name"], file["changed_code"]) for file in pull_files]
        else:
            raise Exception('error on fetch local file %s' % path)
    else:
        if must_in_local:
            raise Exception('not found in local')
        file_list = fetch_file_list(repo, pr_id)

    codeOnlyFileList = filterNonCodeFiles(file_list,path)
    if len(codeOnlyFileList) > 0:
        file_list_cache[ind] = codeOnlyFileList
    return codeOnlyFileList

def filterNonCodeFiles(file_list, outfile_prefix):
    newFileList = []
    count = 0
    for f in file_list:
        if count > 500:
            localfile.write_to_file(outfile_prefix + "/toobig.txt", '500file')
            return []
        if not language_tool.is_text(f['name']):
            newFileList.append(f)
            count +=1
    return newFileList

# -------------------About Repo--------------------------------------------------------
def get_repo_PRlist(repo, type, renew):
    api = GitHubAPI()
    save_path = LOCAL_DATA_PATH + '/pr_data/' + repo + '/%s_list.json' % type

    # todo: could be extended to analyze forks in the future
    if type == 'fork':
        save_path = LOCAL_DATA_PATH + '/result/' + repo + '/forks_list.json'

    if (os.path.exists(save_path)) and (not renew):
        print("read from local files and return")
        try:
            return localfile.get_file(save_path)
        except:
            pass

    print('files does not exist in local disk, start to fetch new list for ', repo, type)
    if (type == 'pull') or (type == 'issue'):
        ret = api.request('repos/%s/%ss' % (repo, type), state='all', paginate=True)
    else:
        if type == 'branch':
            type = 'branche'
        ret = api.request('repos/%s/%ss' % (repo, type), True)

    localfile.write_to_file(save_path, ret)
    return ret

def get_repo_info_forPR_experiment(repo, type, renew):
    filtered_result = []
    api = GitHubAPI()
    print(init.local_pr_data_dir + repo + '/pull_list.json')
    save_path = LOCAL_DATA_PATH + '/pr_data/' + repo + '/pull_list.json'

    if (os.path.exists(save_path)) and (not renew):
        try:
            return localfile.get_file(save_path)
        except:
            pass

def fetch_commit(url, renew=False):
    api = GitHubAPI()
    save_path = LOCAL_DATA_PATH + '/pr_data/%s.json' % url.replace('https://api.github.com/repos/', '')
    if os.path.exists(save_path) and (not renew):
        try:
            return localfile.get_file(save_path)
        except:
            pass

    c = api.request(url)
    time.sleep(0.7)
    file_list = []
    for f in c['files']:
        if 'patch' in f:
            file_list.append(fetch_raw_diff.parse_diff(f['filename'], f['patch']))
    localfile.write_to_file(save_path, file_list)
    return file_list


# ------------------About Pull Requests----------------------------------------------------

def get_PR(repo, num, renew=False):
    api = GitHubAPI()
    save_path = LOCAL_DATA_PATH + '/pr_data/%s/%s/api.json' % (repo, num)
    if os.path.exists(save_path) and (not renew):
        try:
            return localfile.get_file(save_path)
        except:
            pass

    r = api.request('repos/%s/pulls/%s' % (repo, num))
    time.sleep(3.0)
    localfile.write_to_file(save_path, r)
    return r


def concat_commits(commits):
    total_message = ''
    for c in commits:
        message = c['commit']['message']
        total_message += message + '\n'
    return total_message


def get_pr_commit(repo, pr_id, renew=False):
    save_path = LOCAL_DATA_PATH + '/pr_data/%s/%s/commits.json' % (repo, pr_id)
    commit_url = 'repos/%s/pulls/%s/commits' % (repo, pr_id)
    if os.path.exists(save_path) and (not renew) and (os.stat(save_path).st_size > 2):
        try:
            return localfile.get_file(save_path)
        except:

            pass
    #     commits = api.request(pull['commits_url'].replace('https://api.github.com/', ''), True)
    api = GitHubAPI()
    commits = api.request(commit_url.replace('https://api.github.com/', ''), paginate=True, state='all')
    time.sleep(0.7)
    localfile.write_to_file(save_path, commits)
    return commits


def get_another_pull(pull, renew=False):
    api = GitHubAPI()
    save_path = LOCAL_DATA_PATH + '/pr_data/%s/%s/another_pull.json' % (
        pull["base"]["repo"]["full_name"], pull["number"])
    if os.path.exists(save_path) and (not renew):
        try:
            return localfile.get_file(save_path)
        except:
            pass

    comments_href = pull["_links"]["comments"]["href"]  # found cites in comments, but checking events is easier.
    comments = api.request(comments_href, True)
    time.sleep(0.7)
    candidates = []
    for comment in comments:
        candidates.extend(get_pr_and_issue_numbers(comment["body"]))
    candidates.extend(get_pr_and_issue_numbers(pull["body"]))

    result = list(set(candidates))

    localfile.write_to_file(save_path, result)
    return result


# def fetch_file_list(pull, renew=False):
def fetch_file_list(repo, num, renew=False):
    api = GitHubAPI()
    # repo, num = pull["base"]["repo"]["full_name"], str(pull["number"])
    outfile_prefix = init.local_pr_data_dir + repo + "/" + str(num)
    save_path = outfile_prefix + '/raw_diff.json'
    if os.path.exists(save_path) and (not renew):
        try:
            return localfile.get_file(save_path)
        except:
            pass
    file_list = []

    li = api.request('repos/%s/pulls/%s/files' % (repo, num), paginate=True)
    time.sleep(0.8)
    for f in li:
        if f.get('changes', 0) <= 5000 and ('filename' in f) and ('patch' in f):
            file_list.append(fetch_raw_diff.parse_diff(f['filename'], f['patch']))

    localfile.write_to_file(save_path, file_list)
    return file_list


pull_commit_sha_cache = {}


def pull_commit_sha(p):
    index = (p["base"]["repo"]["full_name"], p["number"])
    if index in pull_commit_sha_cache:
        return pull_commit_sha_cache[index]
    c = get_pr_commit(p)
    ret = [(x["sha"], x["commit"]["author"]["name"]) for x in
           list(filter(lambda x: x["commit"]["author"] is not None, c))]
    pull_commit_sha_cache[index] = ret
    return ret


# This function checks if the PR has changed too many files
def check_too_big(pull):
    if not ("changed_files" in pull):
        pull = get_PR(pull["base"]["repo"]["full_name"], pull["number"])

    if not ("changed_files" in pull):
        pull = get_PR(pull["base"]["repo"]["full_name"], pull["number"], True)

    if pull["changed_files"] > 50:
        #         print('more than 50 changed files')
        return True
    if (pull["additions"] >= 10000) or (pull["deletions"] >= 10000):
        #         print('more than 10000 Loc changes')
        return True
    return False


def text2list_precheck(func):
    def proxy(text):
        if text is None:
            return []
        ret = func(text)
        return ret

    return proxy


@text2list_precheck
def get_numbers(text):
    # todo previous version, got tons of FP crossreferece numbers
    # nums = list(filter(lambda x: len(x) >= 3, re.findall('([0-9]+)', text)))
    # nums = list(set(nums))

    # use get_pr_and_issue_numbers instead

    return get_pr_and_issue_numbers(text)


@text2list_precheck
def get_version_numbers(text):
    nums = [''.join(x) for x in re.findall('(\d+\.)?(\d+\.)(\d+)', text)]
    nums = list(set(nums))
    return nums


@text2list_precheck
def get_pr_and_issue_numbers(text):
    nums = []
    nums += re.findall('#([0-9]+)', text)
    nums += re.findall('pull\/([0-9]+)', text)
    nums += re.findall('issues\/([0-9]+)', text)
    nums = list(filter(lambda x: len(x) > 0, nums))
    nums = list(set(nums))
    return nums



LOCAL_DATA_PATH = init.LOCAL_DATA_PATH

api = GitHubAPI()

def text2list_precheck(func):
    def proxy(text):
        if text is None:
            return []
        ret = func(text)
        return ret

    return proxy


def get_pull_commit(pull, renew=False):
    save_path = LOCAL_DATA_PATH + '/pr_data/%s/%s/commits.json' % (pull["base"]["repo"]["full_name"], pull["number"])
    if os.path.exists(save_path) and (not renew):
        try:
            return localfile.get_file(save_path)
        except:
            pass

    commits = api.request(pull['commits_url'].replace('https://api.github.com/', ''), paginate=True, state='all')
    time.sleep(0.7)
    localfile.write_to_file(save_path, commits)
    return commits

pull_commit_sha_cache = {}

def pull_commit_sha(p):
    index = (p["base"]["repo"]["full_name"], p["number"])
    if index in pull_commit_sha_cache:
        return pull_commit_sha_cache[index]
    c = get_pull_commit(p)
    ret = [(x["sha"], x["commit"]["author"]["name"]) for x in
           list(filter(lambda x: x["commit"]["author"] is not None, c))]
    pull_commit_sha_cache[index] = ret
    return ret




def fetch_file_list(pull, renew=False):
    repo, num = pull["base"]["repo"]["full_name"], str(pull["number"])
    save_path = LOCAL_DATA_PATH + '/pr_data/' + repo + '/' + num + '/raw_diff.json'

    if os.path.exists(save_path) and (not renew):
        try:
            return localfile.get_file(save_path)
        except:
            pass

    # t = api.get('repos/%s/pulls/%s/files?page=3' % (repo, num))
    t = api.request('repos/%s/pulls/%s/files?page=3' % (repo, num))
    file_list = []
    if len(t) > 0:
        raise Exception('too big', pull['html_url'])
    else:
        li = api.request('repos/%s/pulls/%s/files' % (repo, num), paginate=True)
        # li = api.request( 'repos/%s/pulls/%s/files' % (repo, num), True)
        time.sleep(0.8)
        for f in li:
            if f.get('changes', 0) <= 5000 and ('filename' in f) and ('patch' in f):
                file_list.append(parse_diff(f['filename'], f['patch']))

    localfile.write_to_file(save_path, file_list)
    return file_list

def allNonCodeFiles(pull):

    # file_list = fetch_file_list(pull)
    try:
        file_list = fetch_file_list(pull)
    except:
        print("file list too big")
        return True
        # raise Exception('too big', pull['html_url'])

    total_num_files = len(file_list)
    noncode_file_count = 0
    for file in file_list:
        for extension in nonCodeFileExtensionList:
            if file['name'].endswith(extension):
                noncode_file_count += 1
        if (noncode_file_count == total_num_files):
            return True
        else:
            return False



# ------------------About Pull Requests----------------------------------------------------

def get_PR(repo, num, renew=False):
    save_path = LOCAL_DATA_PATH + '/pr_data/%s/%s/api.json' % (repo, num)
    if os.path.exists(save_path) and (not renew):
        try:
            return localfile.get_file(save_path)
        except:
            pass

    r = api.request('repos/%s/pulls/%s' % (repo, num))
    time.sleep(3.0)
    localfile.write_to_file(save_path, r)
    return r


def get_pull_commit(pull, renew=False):
    save_path = LOCAL_DATA_PATH + '/pr_data/%s/%s/commits.json' % (pull["base"]["repo"]["full_name"], pull["number"])
    if os.path.exists(save_path) and (not renew):
        try:
            return localfile.get_file(save_path)
        except:

            pass
    #     commits = api.request(pull['commits_url'].replace('https://api.github.com/', ''), True)

    commits = api.request(pull['commits_url'].replace('https://api.github.com/', ''), paginate=True, state='all')
    # commits = api.request(pull['commits_url'].replace('https://api.github.com/', paginate=True, state='all'))
    time.sleep(0.7)
    localfile.write_to_file(save_path, commits)
    return commits


@text2list_precheck
def get_pr_and_issue_numbers(text):
    nums = []
    nums += re.findall('#([0-9]+)', text)
    nums += re.findall('pull\/([0-9]+)', text)
    nums += re.findall('issues\/([0-9]+)', text)
    nums = list(filter(lambda x: len(x) > 0, nums))
    nums = list(set(nums))
    return nums




