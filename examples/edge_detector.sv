// Rising and falling edge pulses from a delayed copy of the input.

module edge_detector (
    input  logic clk,
    input  logic sig,
    output logic rise,
    output logic fall
);
  logic sig_d;

  always_ff @(posedge clk) sig_d <= sig;

  assign rise = sig & ~sig_d;
  assign fall = ~sig & sig_d;
endmodule
