// A PWM generator: a free-running counter compared against a duty cycle.

module pwm (
    input  logic       clk,
    input  logic [3:0] duty,
    output logic       out
);
  logic [3:0] count;

  always_ff @(posedge clk) count <= count + 1'b1;

  assign out = count < duty;
endmodule
