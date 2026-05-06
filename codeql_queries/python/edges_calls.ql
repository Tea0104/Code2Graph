import python
import semmle.python.types.FunctionObject

// 查询函数调用关系，输出调用者和被调用者的文件、名称和行号。

from FunctionObject caller, FunctionObject callee
where callee = caller.getACallee()
select
"CALLS" as rel,
  caller.getFunction().getLocation().getFile().getRelativePath() as fromFile,
  caller.getFunction().getName() as fromName,
  caller.getFunction().getLocation().getStartLine() as fromLine,
  callee.getFunction().getLocation().getFile().getRelativePath() as toFile,
  callee.getFunction().getName() as toName,
  callee.getFunction().getLocation().getStartLine() as toLine