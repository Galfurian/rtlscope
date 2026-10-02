// A 4-bit linear feedback shift register.

module lfsr (
    input  logic       clk,
    input  logic       rst,
    output logic [3:0] state
);
  logic feedback;

  assign feedback = state[3] ^ state[2];

  always_ff @(posedge clk) begin
    if (rst) state <= 4'b0001;
    else state <= {state[2:0], feedback};
  end
endmodule
