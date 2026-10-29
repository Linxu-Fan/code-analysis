"""Geometric shapes."""
import math

PI2 = math.pi * 2


class Shape:
    """Base shape."""
    count = 0

    def __init__(self, name):
        self.name = name
        Shape.count += 1

    def area(self):
        raise NotImplementedError

    def describe(self):
        # describe the shape
        return f"{self.name}: {self.area():.2f}"


class Circle(Shape):
    def __init__(self, r):
        super().__init__("circle")
        self.r = r

    def area(self):
        return math.pi * self.r ** 2


class Rect(Shape):
    def __init__(self, w, h):
        super().__init__("rect")
        self.w, self.h = w, h

    def area(self):
        if self.w < 0 or self.h < 0:
            return 0
        return self.w * self.h
