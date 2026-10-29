from pkg.shapes import Circle, Rect
from pkg import shapes as sh


def build(n):
    items = []
    for i in range(n):
        if i % 2 == 0:
            items.append(Circle(i))
        elif i % 3 == 0:
            items.append(Rect(i, i + 1))
        else:
            items.append(sh.Rect(1, 1))
    return items


def total(items):
    t = 0
    for s in items:
        t += s.area()
    return t


def unused_helper(x):
    return x * 2


if __name__ == "__main__":
    shapes = build(10)
    print(total(shapes))
    c = Circle(2)
    print(c.describe())
