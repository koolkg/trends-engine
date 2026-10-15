import { render, screen } from "@testing-library/react";
import { Sparkline } from "../src/components/Sparkline";

test("one point per hour, last point marked", () => {
  const { container } = render(<Sparkline points={[1, 3, 2, 8]} />);
  expect(screen.getByRole("img")).toHaveAccessibleName("Posts per hour, last 4 hours: 1, 3, 2, 8");
  expect(container.querySelector("polyline")!.getAttribute("points")!.split(" ")).toHaveLength(4);
  expect(container.querySelector("circle")).toHaveAttribute("cx", "64");
});

test("flat or too-short series don't break", () => {
  const { container } = render(<Sparkline points={[0, 0, 0]} />);
  expect(container.querySelector("polyline")).not.toBeNull();
  const short = render(<Sparkline points={[4]} />);
  expect(short.container.querySelector("polyline")).toBeNull();
});
