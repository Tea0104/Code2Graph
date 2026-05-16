import python

class RangeFunction extends Function {
  predicate hasLocationInfo(
    string filepath, int startline, int startcolumn, int endline, int endcolumn
  ) {
    super.getLocation().hasLocationInfo(filepath, startline, startcolumn, _, _) and
    this.getBody().getLastItem().getLocation().hasLocationInfo(filepath, _, _, endline, endcolumn)
  }
}

class RangeClass extends Class {
  predicate hasLocationInfo(
    string filepath, int startline, int startcolumn, int endline, int endcolumn
  ) {
    super.getLocation().hasLocationInfo(filepath, startline, startcolumn, _, _) and
    this.getBody().getLastItem().getLocation().hasLocationInfo(filepath, _, _, endline, endcolumn)
  }
}