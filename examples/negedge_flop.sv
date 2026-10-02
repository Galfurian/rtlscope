// Flip-flops on both edges of one clock.

module negedge_flop (
    input  logic clk,
    input  logic d,
    output logic q_rise,
    output logic q_fall
);
  always_ff @(posedge clk) q_rise <= d;
  always_ff @(negedge clk) q_fall <= d;
endmodule
