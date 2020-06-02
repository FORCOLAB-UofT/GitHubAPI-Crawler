from github_api import GitHubAPI

if __name__ == "__main__":
    api = GitHubAPI()
    # # query github api with URL
    #     # res = api.request("repos/jquery/jquery/pulls/4406/commits")
    #     #

    # query issue/pr timeline
    # events = api.get_issue_pr_timeline("jquery/jquery", 4406)


    #query repo
    res = api.get_repo("Jupyter%20Notebook","2008-01-01","2009-01-01")
    print()
