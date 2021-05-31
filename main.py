from github_api import GitHubAPI

if __name__ == "__main__":
    api = GitHubAPI()
    # # query github api with URL https://api.github.com/repos/jquery/jquery/pulls/4406/commits
    #     # res = api.request("repos/jquery/jquery/pulls/4406/commits")
    #     #

    # query issue/pr timeline
    #  api doc: https://developer.github.com/v3/issues/timeline/#list-timeline-events-for-an-issue
    # the following query the events for https://github.com/jquery/jquery/pull/4406/
    # events = api.get_issue_pr_timeline("jquery/jquery", 4406)


    #Search repos
    res = api.get_repo("Jupyter%20Notebook","2008-01-01","2009-01-01")