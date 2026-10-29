#include "shape.h"
#include <sstream>

namespace geo {

int Shape::instances_ = 0;

Shape::Shape(const std::string& name) : name_(name) {
    ++instances_;
}

Shape::~Shape() {
    --instances_;
}

std::string Shape::describe() const {
    std::ostringstream out;
    out << name_ << ": " << area();
    return out.str();
}

int Shape::count() { return instances_; }

Circle::Circle(double r) : Shape("circle"), r_(r) {}

double Circle::area() const {
    return 3.14159265 * r_ * r_;
}

Rect::Rect(double w, double h) : Shape("rect"), w_(w), h_(h) {}

double Rect::area() const {
    if (w_ < 0 || h_ < 0) {
        return 0.0;
    }
    return w_ * h_;
}

bool Rect::isSquare() const { return w_ == h_; }

}  // namespace geo
