import python
import semmle.python.types.FunctionObject
import Extents

// 查询函数调用关系，输出调用者和被调用者的文件、名称和行号。

predicate callableNode(string file, string name, int startLine) {
  exists(RangeFunction c |
    c.isMethod() and
    c.hasLocationInfo(file, startLine, _, _, _) and
    name = c.getName()
  )
  or
  exists(RangeFunction c |
    not c.isMethod() and
    c.hasLocationInfo(file, startLine, _, _, _) and
    name = c.getName()
  )
}

from FunctionObject caller, FunctionObject callee
where
  callee = caller.getACallee() and
  callableNode(
    caller.getFunction().getLocation().getFile().getRelativePath(),
    caller.getFunction().getName(),
    caller.getFunction().getLocation().getStartLine()
  ) and
  callableNode(
    callee.getFunction().getLocation().getFile().getRelativePath(),
    callee.getFunction().getName(),
    callee.getFunction().getLocation().getStartLine()
  )
select
"CALLS" as rel,
  caller.getFunction().getLocation().getFile().getRelativePath() as fromFile,
  caller.getFunction().getName() as fromName,
  caller.getFunction().getLocation().getStartLine() as fromLine,
  callee.getFunction().getLocation().getFile().getRelativePath() as toFile,
  callee.getFunction().getName() as toName,
  callee.getFunction().getLocation().getStartLine() as toLine