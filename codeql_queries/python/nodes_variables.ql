import python

// 查询 Python 类属性和全局变量节点，输出名称、文件和位置。

predicate variableNode(string kind, string file, string name, int startLine, int endLine) {
  exists(Class c, Attribute v |
    v.getScope() = c and
    kind = "ClassAttribute" and
    file = v.getLocation().getFile().getRelativePath() and
    name = v.getName() and
    startLine = v.getLocation().getStartLine() and
    endLine = v.getLocation().getEndLine()
  )
  or
  exists(Class c, AnnAssign a, Name target |
    a.getScope() = c and
    target = a.getTarget() and
    kind = "ClassAttribute" and
    file = a.getLocation().getFile().getRelativePath() and
    name = target.getId() and
    startLine = a.getLocation().getStartLine() and
    endLine = a.getLocation().getEndLine()
  )
  or
  exists(Module m, AssignStmt a, GlobalVariable v |
    a.defines(v) and
    a.getScope() = m and
    kind = "GlobalVariable" and
    file = a.getLocation().getFile().getRelativePath() and
    name = v.getId() and
    startLine = a.getLocation().getStartLine() and
    endLine = a.getLocation().getEndLine()
  )
}

from string kind, string file, string name, int startLine, int endLine
where variableNode(kind, file, name, startLine, endLine)
select kind, file, name, startLine, endLine
