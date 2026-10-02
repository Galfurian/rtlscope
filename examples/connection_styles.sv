// The ways a port can be connected: named, positional, .name, .* and left open.

module stage (
    input  logic clk,
    input  logic d,
    output logic q,
    output logic q_n
);
  always_ff @(posedge clk) q <= d;
  assign q_n = ~q;
endmodule

module connection_styles (
    input  logic clk,
    input  logic d,
    output logic q,
    output logic q_n
);
  logic s1, s2;

  stage u_named      (.clk(clk), .d(d), .q(s1), .q_n());
  stage u_positional (clk, s1, s2, );
  stage u_dot_name   (.clk, .d(s2), .q, .q_n);
endmodule
