import python

// 查询 Python 全局变量、局部变量和属性节点，输出名称、文件和位置。

predicate variableNode(string kind, string file, string name, int startLine, int endLine) {
  exists(GlobalVariable v |
    kind = "GlobalVariable" and
    file = v.getAnAccess().getLocation().getFile().getRelativePath() and
    name = v.getId() and
    startLine = v.getAnAccess().getLocation().getStartLine() and
    endLine = v.getAnAccess().getLocation().getEndLine()
  )
  or
  exists(LocalVariable v |
    kind = "LocalVariable" and
    file = v.getAnAccess().getLocation().getFile().getRelativePath() and
    name = v.getId() and
    startLine = v.getAnAccess().getLocation().getStartLine() and
    endLine = v.getAnAccess().getLocation().getEndLine()
  )
  or
  exists(Attribute v |
    kind = "Attribute" and
    file = v.getLocation().getFile().getRelativePath() and
    name = v.getName() and
    startLine = v.getLocation().getStartLine() and
    endLine = v.getLocation().getEndLine()
  )
}

from string kind, string file, string name, int startLine, int endLine
where variableNode(kind, file, name, startLine, endLine)
select kind, file, name, startLine, endLine
