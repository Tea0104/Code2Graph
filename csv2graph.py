import csv
import os
from pathlib import Path
from dataclasses import dataclass,asdict


# 路径归一化：Extents.qll 返回绝对路径，其他查询返回相对路径，
# 统一砍掉共同前缀，使所有路径为相对路径。
_SOURCE_PREFIX = None


def _detect_prefix():
    """扫描所有 CSV 数据，检测绝对路径的共同前缀。"""
    global _SOURCE_PREFIX
    abs_dirs = set()
    rel_files = set()
    for rows in result_files.values():
        for row in rows:
            for key in ["file", "fromFile", "toFile"]:
                v = row.get(key, "")
                if not v:
                    continue
                v = v.replace("\\", "/")
                if ":/" in v:
                    abs_dirs.add(os.path.dirname(v))
                else:
                    rel_files.add(v)
    # 绝对路径去掉哪个前缀后能匹配到相对路径
    for d in sorted(abs_dirs, key=len, reverse=True):
        prefix = d + "/"
        # 测试：所有绝对路径去掉前缀后是否都出现在相对路径集合中
        matched = True
        count = 0
        for rows in result_files.values():
            for row in rows:
                for key in ["file", "fromFile", "toFile"]:
                    v = row.get(key, "").replace("\\", "/")
                    if v.startswith(prefix):
                        count += 1
                        if v[len(prefix):] not in rel_files:
                            matched = False
                            break
        if matched and count > 0:
            _SOURCE_PREFIX = prefix
            return
    _SOURCE_PREFIX = ""


def _normalize_path(filepath: str) -> str:
    """将绝对路径转换为相对路径。"""
    if not filepath:
        return filepath
    fp = filepath.replace("\\", "/")
    if _SOURCE_PREFIX and fp.startswith(_SOURCE_PREFIX):
        return fp[len(_SOURCE_PREFIX):]
    return fp

#读单个csv文件
def read_csv(path:Path):
    #判断是csv还是tsv（分隔符是 , 还是 \t）
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","

    with path.open(encoding="utf-8-sig",newline="")as handle:
        #创建字典，第一行是表头，后续每行映射成字典
        reader=csv.DictReader(handle,delimiter=delimiter)

        #保存数据
        all_rows=[]
        for row in reader:
            all_rows.append(row)
        
        return all_rows
    

result_files={
    "nodes_modules": [],
    "nodes_classes": [],
    "nodes_callables": [],
    "nodes_imports": [],
    "nodes_parameters": [],
    "nodes_classAttribute": [],
    #"nodes_variables": [],
    "edges_calls":[],
    "edges_defines":[],
    "edges_imports":[],
    "edges_inherits":[],
    "edges_references":[],
}

#对result_files的引用
NODES = {
    "nodes_modules": result_files["nodes_modules"],
    "nodes_classes": result_files["nodes_classes"],
    "nodes_callables": result_files["nodes_callables"],
    "nodes_imports": result_files["nodes_imports"],
    "nodes_parameters": result_files["nodes_parameters"],
    "nodes_classAttribute": result_files["nodes_classAttribute"],
    #"nodes_variables": result_files["nodes_variables"],
}

EDGES = {
    "edges_calls":result_files["edges_calls"],
    "edges_defines":result_files["edges_defines"],
    "edges_imports":result_files["edges_imports"],
    "edges_inherits":result_files["edges_inherits"],
    "edges_references":result_files["edges_references"],
}

#按照查询文件名称分别保存查询文件的结果
def load_csv(path):
    p=Path(path)
    prefix=p.stem
    all_rows=read_csv(path)
    result_files[prefix].extend(all_rows)


#存入查询内容到result_files之后
#1.创建所有节点
nodes={} #nodes={"node_id":{row},...}

@dataclass
class Node:
    id:str
    kind:str 
    name:str
    file:str
    startline:int
    endline:int

    def to_dict(self):
        return asdict(self)
    

def create_nodes():
    _detect_prefix()
    for key in NODES.values():
        for value in key:
            kind=value["kind"]
            name=value["name"]
            file=_normalize_path(value["file"])
            startline=value["startLine"]
            endline=value["endLine"]
            node_id=f"{file}:{name}:{startline}"
            node=Node(node_id,kind,name,file,startline,endline).to_dict()
            nodes[node_id]=node

#2.加入边
edges={}

@dataclass
class Edge:  #保存了起点、终点，用于建图
    edge_id:str
    source:str
    target:str
    kind:str

    def to_dict(self):
        return asdict(self)


def ensure_node(node_id: str, file: str, name: str, line: str | int | None):
    if node_id in nodes:
        return nodes[node_id]
    
#这里会把不存在的节点放到node集合中

def create_edges():
    for key in EDGES.values():
        for value in key:
            rel=value["rel"]
            fromFile=_normalize_path(value["fromFile"])
            fromName=value["fromName"]
            fromLine=value["fromLine"]
            toFile=_normalize_path(value["toFile"])
            toName=value["toName"]
            toLine=value["toLine"]
            source_id=f"{fromFile}:{fromName}:{fromLine}"
            target_id=f"{toFile}:{toName}:{toLine}"
            edge_id=f"{source_id}:{target_id}:{rel}"
            source=ensure_node(source_id, fromFile, fromName, fromLine)
            target=ensure_node(target_id, toFile, toName, toLine)
            edge=Edge(edge_id,source,target,rel).to_dict()
            edges[edge_id]=edge



