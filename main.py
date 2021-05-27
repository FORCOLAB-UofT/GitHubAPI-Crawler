from github_api import GitHubAPI
import re

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

    # jupyter notebook, description is sklearn, pytorch, scikit-learn, tensorflow, caffe
    # 5 year range from today
    res = api.get_repo("Python","2016-05-21","2021-05-21")
    #res = api.get_repo("Jupyter%20Notebook", "2008-01-01","2009-01-01")

    f = open("repositories_python.txt","w")
    for i in range(len(res['items'])):
        desc = res['items'][i]['description']
        name = res['items'][i]['name']
        if desc is not None and (re.search(r"(?i)scikit.*learn",desc) or re.search(r"(?i)tensorflow",desc) or re.search(r"(?i)pytorch",desc) or re.search(r"(?i)sklearn",desc) or re.search(r"(?i)caffe",desc) or re.search(r"(?i)machine.*learning",desc) or re.search(r"(?i)deep.*learning",desc) or re.search(r"(?i)predict(ion)?",desc)
                                 or re.search(r"(?i)scikit.*learn",name) or re.search(r"(?i)tensorflow",name) or re.search(r"(?i)pytorch",name) or re.search(r"(?i)sklearn",name) or re.search(r"(?i)caffe",name) or re.search(r"(?i)machine.*learning",name) or re.search(r"(?i)deep.*learning",name)):
            f.write(res['items'][i]['html_url'])
            f.write('\n')

    f.close()
