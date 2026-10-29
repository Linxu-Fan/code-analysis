#ifndef SHAPE_H
#define SHAPE_H

#include <string>

namespace geo {

/// Abstract base class of all shapes.
class Shape {
public:
    explicit Shape(const std::string& name);
    virtual ~Shape();
    virtual double area() const = 0;
    std::string describe() const;
    static int count();

protected:
    std::string name_;

private:
    static int instances_;
};

class Circle : public Shape {
public:
    explicit Circle(double r);
    double area() const override;

private:
    double r_;
};

class Rect : public Shape {
public:
    Rect(double w, double h);
    double area() const override;
    bool isSquare() const;

private:
    double w_, h_;
};

}  // namespace geo

#endif
