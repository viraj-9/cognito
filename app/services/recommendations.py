"""Resource clustering and safe, feedback-aware learning suggestions."""
from collections import Counter
import math
from .resources import RESOURCES, public_resource
from .text import char_ngrams


def cluster_resources(k: int = 3) -> dict:
    rows=RESOURCES; k=max(2,min(k,len(rows)-1))
    vocab=sorted({gram for row in rows for gram in char_ngrams(" ".join([row["title"],row["summary"],*row["tags"]]))})
    points=[]
    for row in rows:
        counts=Counter(char_ngrams(" ".join([row["title"],row["summary"],*row["tags"]])))
        norm=math.sqrt(sum(v*v for v in counts.values())) or 1
        points.append([counts.get(term,0)/norm for term in vocab])
    def dist(a,b): return sum((x-y)**2 for x,y in zip(a,b))
    centers=[points[round(i*(len(points)-1)/(k-1))][:] for i in range(k)]
    assignments=[0]*len(points)
    for _ in range(15):
        assignments=[min(range(k),key=lambda c:dist(point,centers[c])) for point in points]
        for cluster in range(k):
            members=[points[i] for i,label in enumerate(assignments) if label==cluster]
            if members: centers[cluster]=[sum(row[j] for row in members)/len(members) for j in range(len(vocab))]
    inertia=sum(dist(point,centers[label]) for point,label in zip(points,assignments))
    silhouettes=[]
    for i,point in enumerate(points):
        own=[math.sqrt(dist(point,points[j])) for j,label in enumerate(assignments) if label==assignments[i] and j!=i]
        if not own: silhouettes.append(0); continue
        a=sum(own)/len(own); other=[]
        for group in range(k):
            members=[math.sqrt(dist(point,points[j])) for j,label in enumerate(assignments) if label==group and j!=i]
            if group!=assignments[i] and members: other.append(sum(members)/len(members))
        b=min(other) if other else 0
        silhouettes.append((b-a)/max(a,b) if max(a,b) else 0)
    return {"assignments":{row["id"]:assignments[i] for i,row in enumerate(rows)},"cluster_sizes":[assignments.count(c) for c in range(k)],"inertia":round(inertia,4),"silhouette":round(sum(silhouettes)/len(silhouettes),4),"method":"character n-gram K-means","examples":len(rows)}


def recommend(conn, user_id: int | None, level: str | None = None, limit: int = 4) -> list[dict]:
    completed=set(); prior_scores={}; cluster_rewards={}; total_feedback=0
    clusters=cluster_resources()
    if user_id is not None:
        completed={row[0] for row in conn.execute("SELECT resource_id FROM progress WHERE user_id=? AND status='completed'",(user_id,))}
        for row in conn.execute("SELECT resource_id,AVG(helpful) AS reward,COUNT(*) AS n FROM recommendation_feedback WHERE user_id=? GROUP BY resource_id",(user_id,)):
            cluster=clusters["assignments"].get(row["resource_id"])
            if cluster is not None:
                reward_sum,observations=cluster_rewards.get(cluster,(0.0,0))
                cluster_rewards[cluster]=(reward_sum+row["reward"]*row["n"],observations+row["n"])
            total_feedback+=row["n"]
        for row in conn.execute("SELECT resource_id,MAX(score) AS best FROM assessment_attempts WHERE user_id=? GROUP BY resource_id",(user_id,)):
            prior_scores[row["resource_id"]]=row["best"]
    # Feedback acts as a conservative contextual-bandit reward: positive ratings
    # promote that topic, negative ratings demote it. No live random exploration.
    def rank(row):
        prerequisites=set(row["prerequisites"])
        prereq_score=sum(1 for value in prerequisites if value in completed)
        cluster=clusters["assignments"][row["id"]]
        observed_reward,observations=cluster_rewards.get(cluster,(0.0,0))
        mean_reward=(observed_reward+.5)/(observations+1)
        # Bounded UCB gives new topics a small chance to be recommended; feedback
        # is explicit, and the limited exploration only changes suggestions.
        explore=.15*math.sqrt(math.log(total_feedback+2)/(observations+1)) if total_feedback else 0.0
        topic_reward=min(1.0,mean_reward+explore)
        assessment_bonus=prior_scores.get(row["id"],0)*.12
        return ((.25 if row["id"] not in completed else -.15)+.10*prereq_score+.10*topic_reward+assessment_bonus, row["id"])
    pool=[row for row in RESOURCES if (not level or row["level"]==level) and row["id"] not in completed]
    if not pool: pool=[row for row in RESOURCES if not level or row["level"]==level]
    pool.sort(key=lambda row:(-rank(row)[0],rank(row)[1]))
    return [public_resource(row,include_exercise=True) for row in pool[:max(1,min(limit,10))]]
