#include <iostream>
#include <vector>
#include "shape.h"
#include "util.h"

using namespace geo;

static int classify(int n) {
    int kind = 0;
    for (int i = 0; i < n; i++) {
        if (i % 2 == 0 && i > 2) {
            kind += 1;
        } else if (i % 3 == 0) {
            kind += 2;
        } else {
            switch (i) {
            case 1: kind--; break;
            case 5: kind++; break;
            default: break;
            }
        }
    }
    return kind;
}

int main(int argc, char** argv) {
    std::vector<Shape*> shapes;
    shapes.push_back(new Circle(1.0));
    Rect* r = new Rect(2.0, 3.0);
    shapes.push_back(r);
    double total = 0;
    for (Shape* s : shapes) {
        total += s->area();
        std::cout << s->describe() << std::endl;
    }
    if (r->isSquare()) {
        log_message("square");
    }
    std::cout << total << " " << classify(argc) << " " << Shape::count() << std::endl;
    return clamp(argc, 0, MAX_ITEMS);
}
